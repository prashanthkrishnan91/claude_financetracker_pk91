"""Agent orchestrator — enforces DB → Context Builder → Single LLM → Persist.

Pipeline guarantees (see tasks/todo.md):
  1. Positions + latest insights + macro are aggregated into ONE structured
     context object by `services.ai.context_builder.build_portfolio_context`.
  2. That context feeds a SINGLE Claude call, serialised behind a module-level
     semaphore. No per-ticker loops. No asyncio.gather over LLM calls.
  3. Results are persisted to agent_insights + recommendations in one pass.

If the context builder returns an empty portfolio the run completes with
status='no_data' and the LLM is not invoked.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from ...database import get_supabase_client
from ..ai.context_builder import build_portfolio_context, build_context_from_inputs
from ..ai import io_layer
from ..intelligence import (
    AnalystVerdict,
    ModeDecision,
    PortfolioSynthesis,
    RunCostTracker,
    RunMode,
    action_to_suggested_action,
    analyze_portfolio,
    build_degraded_verdicts,
    build_features,
    build_market_snapshots,
    classify_run_mode,
    fetch_benchmark_price_action,
    format_thesis,
    persist_features,
    persist_snapshots,
    projected_full_mode_cost,
    synthesize_portfolio,
)
from ..market_data.system_mode import SystemMode, get_system_mode_manager
from .data_sources import get_provider_status
from .llm import LLMClient, FALLBACK_MODEL
from .state import AgentState, TickerInsight

logger = logging.getLogger(__name__)


# Module-level lock: enforces a single LLM call in flight per process for the
# orchestration pipeline. Retries live at the HTTP layer inside LLMClient, not
# at the orchestration layer.
LLM_SEMAPHORE = asyncio.Semaphore(1)


LIGHTWEIGHT_PROMPT_APPENDIX = """

SYSTEM MODE: LIGHTWEIGHT — the upstream data pipeline is degraded. You are
operating on CACHED snapshots only, with no fresh news, fundamentals, or
live prices. You MUST:

  * Default every ticker to HOLD unless an explicit BUY/SELL signal is
    preserved in the cached fields you received.
  * Use low/partial confidence labels for every card. No "high" labels.
  * Cap |conviction| at 0.3 for every ticker regardless of confidence_cap.
  * Do NOT invent headlines, macro commentary, or speculative narratives.
  * Flag the degraded mode in the portfolio summary ("operating on cached
    snapshots — fresh data unavailable") so the user understands the
    constraint.
"""

DEGRADED_PROMPT_APPENDIX = """

SYSTEM MODE: DEGRADED — one or more providers are rate-limited. Some fields
may be missing; cap |conviction| at 0.6 and prefer "partial signal"
confidence labels. Mention the provider degradation in the portfolio
summary when material.
"""


PORTFOLIO_AGENT_CONTRACT = """You are the Portfolio Agent for a long-term retail investor.

You receive a JSON object with these keys:
  - "portfolio": array of position objects (ticker, shares, avg_cost,
    current_price, category, sentiment_label, technical_signal,
    fundamental_score, trend, confidence_score, confidence_label,
    data_completeness_score, missing_fields, confidence_cap,
    data_quality, what_changed). Every ticker is GUARANTEED to have a
    value for each signal field — missing upstream data has already been
    filled with deterministic fallbacks and the gaps are recorded in
    `data_quality.missing_fields`. ``confidence_cap`` is the maximum
    absolute value ``conviction`` may take for that ticker — you MUST
    respect it.
  - "data_quality": {"completeness_score": 0..1, "missing_fields": [...],
    "fallbacks_used": bool, "missing_prices": [tickers],
    "missing_news": [tickers], "missing_fundamentals": [tickers],
    "missing_technicals": [tickers]} — portfolio-wide trust dial. Scale
    your own confidence language down when completeness_score is low;
    for tickers that appear in a missing_* list, explicitly call out the
    data gap in the reasoning (e.g. "no fresh news available").
  - "macro": {"summary": string describing the current market regime,
    "regime", "inflation", "rates", "sentiment", "fallback"}. When
    "fallback" is true, you must not fabricate specific macro figures.
  - "sentiment": portfolio-level sentiment roll-up {bullish_count,
    neutral_count, bearish_count, average_score}
  - "insights": array of prior-run signals per ticker (sentiment/technical/
    fundamental labels)

Rules (MANDATORY):
  * Do NOT infer missing market data. Only reason on the fields provided —
    if a ticker has no news, do not invent headlines; if fundamentals are
    empty, do not estimate P/E, margins, or growth.
  * Respect every ticker's ``confidence_cap``:
      - data_completeness_score < 0.4  →  |conviction| ≤ 0.3
      - data_completeness_score 0.4-0.7 → |conviction| ≤ 0.6
      - data_completeness_score > 0.7   → normal range [-1.0, +1.0]
  * When a ticker's ``confidence_cap`` is ≤ 0.3, default to HOLD unless an
    explicit signal survives in the data you DO have.

Analyse the full portfolio as a whole — you do NOT get a second call. For
each ticker decide BUY / HOLD / SELL with a confidence (high/medium/low),
a conviction score in [-1.0, +1.0] (capped by ``confidence_cap``), and a
2-sentence reasoning that cites sentiment / technical / fundamental
context where available. For tickers whose `confidence_label` is
"watchlist only" or "low confidence signal", default to HOLD and note
the data gap in the thesis — NEVER emit the string "insufficient data"
in any field.

HARD REQUIREMENT — you MUST return a card for EVERY ticker in the
"portfolio" array. If signal data is thin, still emit a card with a
conservative HOLD action, a low-confidence label, and a thesis that
honestly names the missing inputs.

Return ONLY this JSON — no preamble, no code fences, no trailing text:
{
  "summary": "2-3 sentence portfolio-level overview in plain language",
  "risks": ["risk 1", "risk 2"],
  "opportunities": ["opportunity 1", "opportunity 2"],
  "top_buys": ["TICKER1", "TICKER2", "TICKER3"],
  "cards": [
    {
      "ticker": "AAPL",
      "action": "BUY",
      "confidence": "high",
      "conviction": 0.65,
      "sentiment_label": "bullish",
      "sentiment_score": 0.4,
      "technical_signal": "BUY",
      "fundamental_score": 0.5,
      "thesis": "2-sentence thesis citing analyst points and action",
      "reasoning": "2-sentence explanation for the action"
    }
  ]
}
"""


@dataclass
class AgentPipelineResult:
    run_id: str
    status: str
    summary: str
    insights: list[TickerInsight]


class AgentOrchestrator:
    """Drives a single agent run end-to-end with exactly one LLM call."""

    def __init__(
        self,
        user_id: UUID,
        deposit_amount: float = 900.0,
        sale_proceeds: float = 0.0,
        price_service=None,
        anthropic_api_key: str = "",
        finnhub_key: str = "",
        polygon_key: str = "",
    ):
        self.user_id = user_id
        self.deposit_amount = deposit_amount
        self.sale_proceeds = sale_proceeds
        self._price_service = price_service
        self._finnhub_key = finnhub_key
        self._polygon_key = polygon_key
        self._llm = LLMClient(api_key=anthropic_api_key)
        self.db = get_supabase_client()
        # Hard guarantee: exactly one LLM call per orchestrator run.
        self._llm_call_count = 0
        # Reliability flags — read by run() for logging and run-record metadata.
        self._llm_skipped = False    # True when LLM was bypassed entirely
        self._fallback_used = False  # True when deterministic recs were returned

    # ── Public API ────────────────────────────────────────────────────────────

    async def create_run(self, tickers: Optional[list[str]] = None) -> str:
        row = {
            "user_id": str(self.user_id),
            "status": "queued",
            "current_agent": "Queued",
            "progress_pct": 0,
            "tickers": tickers or [],
            "deposit_amount": self.deposit_amount,
            "sale_proceeds": self.sale_proceeds,
        }
        result = self.db.table("agent_runs").insert(row).execute()
        return result.data[0]["id"]

    async def run(self, run_id: str) -> AgentPipelineResult:
        """Execute the full pipeline for ``run_id`` with exactly one LLM call.

        Terminal-status guarantee: the agent_runs row always transitions to
        'completed' or 'failed' before returning, even on unexpected errors.

        Execution DAG (each stage logs perf_counter timings):
          1. fetch_live_prices   — cache-backed IO layer (parallel)
          2. build_context       — pure transform, no IO
          3. single_llm_call     — one Claude call behind LLM_SEMAPHORE
          4. persist             — DB writes (agent_insights + recommendations)
        """
        terminal_status_set = False
        run_start = time.perf_counter()
        timings: dict[str, float] = {}
        try:
            logger.info("Agent run started — id=%s user=%s", run_id, self.user_id)
            await self._update_run(
                run_id, status="running", current_agent="Loading portfolio", progress=5
            )

            t0 = time.perf_counter()
            market_bundle = await self._fetch_market_bundle_for_user()
            live_prices = dict(market_bundle.get("live_prices") or {})
            timings["fetch_live_prices_ms"] = round((time.perf_counter() - t0) * 1000, 1)

            await self._update_run(run_id, current_agent="Building context", progress=25)
            t0 = time.perf_counter()
            context = build_portfolio_context(
                user_id=str(self.user_id),
                live_prices=live_prices,
                market_data=market_bundle,
            )
            timings["build_context_ms"] = round((time.perf_counter() - t0) * 1000, 1)

            # Phase 1 — data stabilization. Build a MarketSnapshot per
            # ticker from the resilient bundle, log the fallback chain for
            # each one, and best-effort persist to Supabase. This is
            # PRE-LLM so the feature engine (Phase 2) + the analyst layer
            # (Phase 3) can both read from the same stable shape.
            t0 = time.perf_counter()
            self._snapshots = await self._build_and_persist_snapshots(
                run_id=run_id,
                context=context,
                bundle=market_bundle,
            )
            timings["snapshots_ms"] = round((time.perf_counter() - t0) * 1000, 1)

            # Phase 2 — deterministic feature engine (no LLM). Project
            # the MarketSnapshots into per-ticker FeatureSet rows with
            # trend_regime / momentum_score / volatility_regime /
            # relative_strength. Persisted to ``agent_features`` so the
            # Phase 3 analyst + Phase 4 synthesis stages read the same
            # structured inputs.
            t0 = time.perf_counter()
            self._features = await self._build_and_persist_features(
                run_id=run_id,
                bundle=market_bundle,
            )
            timings["features_ms"] = round((time.perf_counter() - t0) * 1000, 1)

            # Phase 5 — run-mode classification. Based on snapshots
            # because features propagate snapshot quality; snapshots
            # are the authoritative source. Drives DEGRADED-mode
            # cost control for the analyst + synthesis stages below.
            self._mode_decision = classify_run_mode(
                getattr(self, "_snapshots", {}).values()
            )
            self._cost_tracker = RunCostTracker(mode=self._mode_decision.mode)
            logger.info(
                "run_mode decision — mode=%s avg_quality=%.2f "
                "insufficient=%d/%d reason=%s",
                self._mode_decision.mode.value,
                self._mode_decision.avg_quality,
                self._mode_decision.insufficient_count,
                self._mode_decision.total_tickers,
                self._mode_decision.reason,
            )

            # Safety guard: empty portfolio → no LLM call.
            if not context.get("portfolio"):
                await self._update_run(
                    run_id,
                    status="completed",
                    current_agent="No positions",
                    progress=100,
                    summary="No positions in portfolio; nothing to analyse.",
                )
                logger.info("Agent run completed (no_data) — id=%s", run_id)
                terminal_status_set = True
                return AgentPipelineResult(
                    run_id=run_id,
                    status="no_data",
                    summary="No positions.",
                    insights=[],
                )

            state = self._build_state(run_id, context, live_prices)

            await self._update_run(
                run_id, current_agent="Per-ticker analyst", progress=50
            )

            # Phase 3 — per-ticker LLM analyst replaces the monolithic
            # portfolio-agent call as the primary signal path. Each
            # ticker gets a strictly-validated AnalystVerdict with one
            # retry on malformed JSON, falling back to
            # INSUFFICIENT_DATA (never empty dict).
            t0 = time.perf_counter()
            self._verdicts = await self._run_per_ticker_analyst()
            timings["per_ticker_analyst_ms"] = round(
                (time.perf_counter() - t0) * 1000, 1
            )

            await self._update_run(
                run_id, current_agent="Portfolio synthesis", progress=70
            )

            # Phase 4 — dedicated portfolio synthesis. Single LLM call
            # over the per-ticker verdicts + portfolio composition +
            # macro snapshot. Produces strictly-validated cross-ticker
            # insights (portfolio_bias, key_themes, risk_concentrations,
            # overexposure_flags, rebalancing_suggestions). Deterministic
            # fallback guarantees the acceptance-gate minimums on LLM
            # failure.
            t0 = time.perf_counter()
            synthesis = await self._run_portfolio_synthesis(
                context=context,
            )
            self._synthesis = synthesis
            timings["llm_call_ms"] = round((time.perf_counter() - t0) * 1000, 1)

            # Project the synthesis back onto the agent state so the
            # existing persistence / allocation paths keep working:
            # ``portfolio_advice`` preserves the raw dict the legacy
            # code expects, ``pm_summary`` feeds the agent_runs row.
            advice = {
                "summary": synthesis.summary,
                "portfolio_bias": synthesis.portfolio_bias,
                "key_themes": synthesis.key_themes,
                "risk_concentrations": synthesis.risk_concentrations,
                "overexposure_flags": synthesis.overexposure_flags,
                "rebalancing_suggestions": synthesis.rebalancing_suggestions,
                "_used_fallback": synthesis.used_fallback,
                "cards": [],  # analyst verdicts own per-ticker cards now
            }
            state.portfolio_advice = advice
            state.pm_summary = synthesis.summary

            self._confidence_by_ticker = _extract_confidence_from_context(context)

            # Apply per-ticker analyst verdicts as the primary signal
            # source. The Phase 4 synthesis owns portfolio-level
            # narrative only — ticker fields come from the analyst.
            self._apply_verdicts_to_insights(state, self._verdicts)

            await self._update_run(run_id, current_agent="Saving Insights", progress=95)
            t0 = time.perf_counter()
            await self._persist(state)
            timings["persist_ms"] = round((time.perf_counter() - t0) * 1000, 1)

            allocation_map = {
                i.ticker: i.suggested_allocation
                for i in state.insights.values()
                if i.suggested_allocation > 0
            }
            final_summary = (state.portfolio_advice.get("summary", "") or state.pm_summary or "").strip()
            if not final_summary:
                final_summary = (
                    f"Pipeline processed {len(state.insights)} positions "
                    "— full narrative unavailable."
                )
            await self._update_run(
                run_id,
                status="completed",
                current_agent="Completed",
                progress=100,
                summary=final_summary,
                allocation=allocation_map,
                synthesis=getattr(self, "_synthesis", None),
                mode_decision=getattr(self, "_mode_decision", None),
                cost_tracker=getattr(self, "_cost_tracker", None),
            )
            tracker = getattr(self, "_cost_tracker", None)
            decision = getattr(self, "_mode_decision", None)
            if tracker is not None and decision is not None:
                projected_full = projected_full_mode_cost(
                    tracker,
                    ticker_count=decision.total_tickers or 1,
                    model=self._llm.model,
                )
                savings_pct = 0.0
                if projected_full > 0:
                    savings_pct = (
                        (projected_full - tracker.total_cost_usd)
                        / projected_full * 100
                    )
                logger.info(
                    "cost_metrics — mode=%s llm_calls=%d est_cost_usd=%.4f "
                    "projected_full_usd=%.4f savings=%.1f%%",
                    decision.mode.value, tracker.total_calls,
                    tracker.total_cost_usd, projected_full, savings_pct,
                )
            timings["total_ms"] = round((time.perf_counter() - run_start) * 1000, 1)
            run_completeness = float(
                (context.get("data_quality") or {}).get("completeness_score") or 1.0
            )
            logger.info(
                "Agent run completed — id=%s insights=%d llm_calls=%d "
                "llm_skipped=%s fallback_used=%s completeness=%.2f timings=%s",
                run_id, len(state.insights), self._llm_call_count,
                self._llm_skipped, self._fallback_used, run_completeness, timings,
            )
            if self._llm_call_count > 1:
                logger.error(
                    "LLM CALL COUNT VIOLATION — run=%s made %d calls (expected ≤ 1)",
                    run_id, self._llm_call_count,
                )
            terminal_status_set = True

            return AgentPipelineResult(
                run_id=run_id,
                status="completed",
                summary=final_summary,
                insights=list(state.insights.values()),
            )

        except Exception as exc:
            logger.exception("Agent run failed for run %s", run_id)
            fallback_summary = "Analysis temporarily unavailable — please retry."
            await self._update_run(
                run_id,
                status="failed",
                current_agent="Failed",
                progress=100,
                error_message=str(exc)[:500],
                summary=fallback_summary,
            )
            terminal_status_set = True
            return AgentPipelineResult(
                run_id=run_id,
                status="failed",
                summary=fallback_summary,
                insights=[],
            )

        finally:
            if not terminal_status_set:
                logger.warning(
                    "LIFECYCLE VIOLATION: run %s did not reach terminal state — forcing failed",
                    run_id,
                )
                try:
                    await self._update_run(
                        run_id,
                        status="failed",
                        current_agent="Failed",
                        progress=100,
                        error_message="Internal orchestrator error",
                        summary="Analysis temporarily unavailable — please retry.",
                    )
                except Exception as cleanup_exc:
                    logger.warning(
                        "Failed to mark run %s failed in finally block: %s",
                        run_id,
                        cleanup_exc,
                    )

    # ── Single LLM call ──────────────────────────────────────────────────────

    async def _single_llm_call(self, context: dict[str, Any]) -> dict[str, Any]:
        """Execute one Claude call (or deterministic fallback) for this run.

        Guardrails evaluated in order:
          1. No API key          → deterministic fallback immediately.
          2. completeness < 0.6  → skip LLM (cost control), deterministic fallback.
          3. Degraded / LIGHTWEIGHT mode or completeness < 0.75 → downgrade to Haiku.
          4. LLM returns empty / no cards → retry once with stripped prompt (inside semaphore).
          5. Retry still empty   → deterministic fallback. Never returns {}.
        """
        if not self._llm.api_key:
            logger.warning(
                "LLM skipped — no anthropic_api_key (llm_skipped=True fallback_used=True)"
            )
            self._llm_skipped = True
            self._fallback_used = True
            return _generate_deterministic_recs(context)

        self._llm_call_count += 1
        if self._llm_call_count > 1:
            logger.error(
                "Orchestrator attempted a second LLM call within one run — blocked"
            )
            return {}

        # ── Data-quality snapshot for guardrails + logging ────────────────
        data_quality = context.get("data_quality") or {}
        completeness = float(data_quality.get("completeness_score") or 1.0)
        source_status = get_provider_status()
        failed_sources = [k for k, v in source_status.items() if v != "ok"]
        failed_pct = len(failed_sources) / max(1, len(source_status)) if source_status else 0.0

        logger.info(
            "Pipeline data check — completeness=%.2f failed_sources=%s (%.0f%% failed)",
            completeness, failed_sources, failed_pct * 100,
        )

        _inject_confidence_caps(context)
        mode_state = get_system_mode_manager().current()
        context["system_mode"] = mode_state.to_dict()
        if mode_state.mode == SystemMode.LIGHTWEIGHT:
            _force_lightweight_caps(context)

        # ── Guardrail: skip LLM when data is too thin ─────────────────────
        if completeness < 0.6:
            logger.warning(
                "completeness=%.2f < 0.6 — LLM skipped, using deterministic fallback "
                "(failed_pct=%.0f%% mode=%s llm_skipped=True fallback_used=True)",
                completeness, failed_pct * 100, mode_state.mode.value,
            )
            self._llm_skipped = True
            self._fallback_used = True
            return _generate_deterministic_recs(context)

        # ── Cost control: degraded/borderline → Haiku ─────────────────────
        if mode_state.mode in {SystemMode.DEGRADED, SystemMode.LIGHTWEIGHT} or completeness < 0.75:
            logger.info(
                "Downgrading to Haiku — mode=%s completeness=%.2f",
                mode_state.mode.value, completeness,
            )
            self._llm.model = FALLBACK_MODEL

        system_prompt = PORTFOLIO_AGENT_CONTRACT
        if mode_state.mode == SystemMode.LIGHTWEIGHT:
            system_prompt = PORTFOLIO_AGENT_CONTRACT + LIGHTWEIGHT_PROMPT_APPENDIX
        elif mode_state.mode == SystemMode.DEGRADED:
            system_prompt = PORTFOLIO_AGENT_CONTRACT + DEGRADED_PROMPT_APPENDIX

        user_message = json.dumps(context, default=str)

        async with LLM_SEMAPHORE:
            logger.info(
                "LLM call start — model=%s tickers=%d mode=%s completeness=%.2f",
                self._llm.model,
                len(context.get("portfolio") or []),
                mode_state.mode.value,
                completeness,
            )
            response = await self._llm.ask_json(
                system=system_prompt,
                user=user_message,
                max_tokens=3500,
            )

            # Retry once with a stripped prompt when the LLM returned no cards.
            if not response or not response.get("cards"):
                logger.warning(
                    "LLM returned no cards (model=%s completeness=%.2f) — "
                    "retrying with simplified prompt",
                    self._llm.model, completeness,
                )
                response = await self._llm.ask_json(
                    system=system_prompt,
                    user=_build_simplified_prompt(context),
                    max_tokens=2000,
                )

        # ── Final safety net: never return {} ─────────────────────────────
        if not response or not response.get("cards"):
            logger.warning(
                "LLM empty after retry — deterministic fallback "
                "(completeness=%.2f model=%s fallback_used=True)",
                completeness, self._llm.model,
            )
            self._fallback_used = True
            return _generate_deterministic_recs(context)

        return response

    # ── State construction ──────────────────────────────────────────────────

    def _build_state(
        self,
        run_id: str,
        context: dict[str, Any],
        live_prices: dict[str, float],
    ) -> AgentState:
        portfolio = context.get("portfolio") or []
        prior_insights = {i["ticker"]: i for i in (context.get("insights") or []) if i.get("ticker")}

        tickers = [p["ticker"] for p in portfolio if p.get("ticker")]

        state = AgentState(
            user_id=str(self.user_id),
            run_id=run_id,
            tickers=tickers,
            deposit_amount=self.deposit_amount,
            sale_proceeds=self.sale_proceeds,
        )

        # Portfolio totals + category weights
        total = 0.0
        category_totals: dict[str, float] = {}
        rows: list[tuple[dict[str, Any], float, float]] = []
        for p in portfolio:
            shares = float(p.get("shares") or 0)
            avg_cost = float(p.get("avg_cost") or 0)
            price = p.get("current_price") or live_prices.get(p["ticker"]) or avg_cost
            mv = shares * price
            total += mv
            cat = p.get("category") or "Other"
            category_totals[cat] = category_totals.get(cat, 0.0) + mv
            rows.append((p, mv, price))

        state.total_portfolio_value = total
        state.category_weights = {
            k: round((v / total * 100) if total > 0 else 0, 1)
            for k, v in category_totals.items()
        }

        for p, mv, price in rows:
            ticker = p["ticker"]
            shares = float(p.get("shares") or 0)
            avg_cost = float(p.get("avg_cost") or 0)
            pnl_pct = None
            if avg_cost > 0 and price:
                pnl_pct = round((price - avg_cost) / avg_cost * 100, 2)
            weight = round((mv / total * 100) if total > 0 else 0, 2)
            prior = prior_insights.get(ticker, {})
            insight = TickerInsight(
                ticker=ticker,
                name=p.get("name", ticker),
                category=p.get("category", "Other"),
                shares=shares,
                avg_cost=avg_cost,
                current_price=price,
                current_weight_pct=weight,
                pnl_pct=pnl_pct,
                lt_eligible=bool(p.get("lt_eligible", False)),
                target_price=p.get("target_price"),
                sentiment_label=prior.get("sentiment") or None,
                technical_signal=prior.get("technical") or None,
            )
            state.insights[ticker] = insight

        return state

    def _apply_advice_to_insights(self, state: AgentState, advice: dict[str, Any]) -> None:
        """Project the single LLM response onto per-ticker TickerInsight rows.

        Confidence gating is enforced post-hoc here: for each ticker we clamp
        the absolute conviction score to the cap derived from the per-ticker
        ``data_completeness_score``. This is deterministic — the LLM can
        ignore the cap in its system prompt and we still never surface a
        high-conviction action on low-data tickers.
        """
        cards = advice.get("cards") or []
        card_map: dict[str, dict[str, Any]] = {}
        for card in cards:
            t = (card.get("ticker") or "").upper()
            if t:
                card_map[t] = card

        # Source-of-truth for confidence is the context we sent to the LLM —
        # fall back to whatever ``_single_llm_call`` captured for this run.
        confidence_by_ticker = getattr(self, "_confidence_by_ticker", {}) or {}

        for ticker, insight in state.insights.items():
            card = card_map.get(ticker.upper())
            if not card:
                insight.suggested_action = insight.suggested_action or "HOLD"
                if not insight.investment_thesis:
                    pnl_str = (
                        f" Current P&L: {insight.pnl_pct:+.1f}%."
                        if insight.pnl_pct is not None else ""
                    )
                    sent = insight.sentiment_label or "neutral"
                    insight.investment_thesis = (
                        f"{ticker}: portfolio agent issued no specific signal — "
                        f"holding ({sent} sentiment).{pnl_str} "
                        "Awaiting richer market data for a directional call."
                    )
                continue

            insight.suggested_action = _normalize_action(card.get("action"))
            raw_conviction = _clamp(card.get("conviction"), -1.0, 1.0)
            cap = _confidence_cap_for(confidence_by_ticker.get(ticker.upper(), 1.0))
            insight.conviction_score = _apply_cap(raw_conviction, cap)
            insight.sentiment_label = card.get("sentiment_label") or insight.sentiment_label
            insight.sentiment_score = _to_float_or_none(card.get("sentiment_score"))
            insight.sentiment_summary = card.get("sentiment_summary") or ""
            insight.technical_signal = card.get("technical_signal") or insight.technical_signal
            insight.technical_summary = card.get("technical_summary") or ""
            insight.fundamental_score = _to_float_or_none(card.get("fundamental_score"))
            insight.fundamental_summary = card.get("fundamental_summary") or ""
            thesis = card.get("thesis") or card.get("reasoning") or ""
            thesis = str(thesis)[:500]
            if not thesis:
                pnl_str = (
                    f" P&L {insight.pnl_pct:+.1f}%."
                    if insight.pnl_pct is not None else ""
                )
                thesis = (
                    f"{ticker}: {insight.suggested_action} — "
                    f"portfolio agent signal ({insight.sentiment_label or 'neutral'} "
                    f"sentiment, {insight.technical_signal or 'NEUTRAL'} technical).{pnl_str}"
                )
            insight.investment_thesis = thesis

        self._allocate_cash(state)

    def _allocate_cash(self, state: AgentState) -> None:
        """Distribute deposit + sale_proceeds across BUY-rated tickers by conviction."""
        cash = state.cash_to_deploy
        if cash <= 0:
            return
        candidates: list[tuple[TickerInsight, float]] = []
        for insight in state.insights.values():
            if insight.suggested_action != "BUY":
                continue
            conviction = insight.conviction_score or 0.0
            if conviction <= 0:
                continue
            candidates.append((insight, conviction))
        if not candidates:
            return
        total_weight = sum(w for _, w in candidates)
        if total_weight <= 0:
            return
        remaining = cash
        last_idx = len(candidates) - 1
        for i, (insight, weight) in enumerate(candidates):
            if i == last_idx:
                dollars = round(remaining, 2)
            else:
                dollars = round(cash * (weight / total_weight), 2)
                remaining -= dollars
            insight.suggested_allocation = max(0.0, dollars)

    # ── Market bundle bootstrap (cache-first via io_layer) ──────────────────

    async def _fetch_market_bundle_for_user(self) -> dict[str, Any]:
        """Return the full io_layer bundle (prices + news + fundamentals + price_action).

        Phase 1 — the orchestrator now pulls the full bundle instead of
        prices-only so we can project it into :class:`MarketSnapshot`
        rows before the LLM runs. Each upstream source degrades
        independently; a broken finnhub doesn't poison fundamentals.

        The bundle is canonical — ``io_layer.fetch_market_bundle``
        guarantees every key is present even when every upstream fails,
        so callers can destructure without defensive ``.get``.
        """
        if not self._price_service:
            return await io_layer.fetch_market_bundle(
                [], price_service=None
            )
        try:
            tickers = [
                p["ticker"]
                for p in (
                    self.db.table("positions")
                    .select("ticker")
                    .eq("user_id", str(self.user_id))
                    .execute()
                ).data
                or []
                if p.get("ticker")
            ]
        except Exception as exc:
            logger.warning("Failed to list tickers for bundle fetch: %s", exc)
            return await io_layer.fetch_market_bundle(
                [], price_service=None
            )
        if not tickers:
            return await io_layer.fetch_market_bundle(
                [], price_service=None
            )
        try:
            return await io_layer.fetch_market_bundle(
                tickers,
                price_service=self._price_service,
                finnhub_key=self._finnhub_key,
                polygon_key=self._polygon_key,
                include_news=True,
                include_fundamentals=True,
                include_price_action=True,
            )
        except Exception as exc:  # noqa: BLE001 — absolute failure isolation
            logger.warning("io_layer bundle fetch failed, degrading gracefully: %s", exc)
            return await io_layer.fetch_market_bundle(
                [], price_service=None
            )

    async def _fetch_live_prices_for_user(self) -> dict[str, float]:
        """Legacy shim — some callers still want the prices-only projection."""
        bundle = await self._fetch_market_bundle_for_user()
        return dict(bundle.get("live_prices") or {})

    # ── MarketSnapshot stage (Phase 1) ──────────────────────────────────────

    async def _build_and_persist_snapshots(
        self,
        *,
        run_id: str,
        context: dict[str, Any],
        bundle: dict[str, Any],
    ) -> dict[str, Any]:
        """Derive :class:`MarketSnapshot` objects and persist them.

        Best-effort — if the ``market_snapshots`` table is missing, the
        persistence layer logs a single WARNING and the orchestrator
        continues. Builds snapshots before the LLM stage so the feature
        engine (Phase 2) can read the persisted rows.
        """
        portfolio = context.get("portfolio") or []
        tickers = [p.get("ticker") for p in portfolio if p.get("ticker")]
        if not tickers:
            return {}

        prior_insights = {
            entry.get("ticker"): entry for entry in (context.get("insights") or [])
        }
        snapshots = build_market_snapshots(
            bundle,
            tickers=tickers,
            prior_insights=prior_insights,
            positions=portfolio,
        )

        # Emit a single structured log line per ticker so operators can
        # assert the fallback-chain acceptance gate without re-running
        # the full pipeline. Kept to one line per ticker on purpose —
        # verbose enough to triage 429/403 degradations, terse enough
        # not to flood logs when everything succeeds.
        for ticker, snap in snapshots.items():
            logger.info(
                "snapshot_fallbacks ticker=%s source=%s chain=%s quality=%.2f "
                "missing=%s",
                ticker,
                snap.price_source,
                "→".join(snap.fallback_chain) if snap.fallback_chain else "none",
                snap.data_quality_score,
                ",".join(snap.missing_fields) if snap.missing_fields else "none",
            )

        try:
            inserted = await asyncio.to_thread(
                persist_snapshots,
                list(snapshots.values()),
                run_id=run_id,
                user_id=str(self.user_id),
            )
            logger.info(
                "market_snapshots persisted — run=%s tickers=%d inserted=%d",
                run_id, len(snapshots), inserted,
            )
        except Exception as exc:  # noqa: BLE001 — never fail a run on persistence
            logger.warning("market_snapshots persist raised (swallowed): %s", exc)

        return snapshots

    # ── Feature engine stage (Phase 2) ──────────────────────────────────────

    async def _build_and_persist_features(
        self,
        *,
        run_id: str,
        bundle: dict[str, Any],
    ) -> dict[str, Any]:
        """Derive :class:`FeatureSet` per ticker and persist best-effort.

        Called immediately after :meth:`_build_and_persist_snapshots` so
        the feature engine can project the same snapshots onto the
        deterministic feature rows downstream stages consume. The
        benchmark (SPY by default) is fetched through the cache-first
        helper — a failed fetch degrades ``relative_strength`` to
        absolute momentum without breaking the run.
        """
        snapshots = getattr(self, "_snapshots", {}) or {}
        if not snapshots:
            return {}

        try:
            benchmark = await fetch_benchmark_price_action("SPY")
        except Exception as exc:  # noqa: BLE001 — benchmark is best-effort
            logger.warning("benchmark fetch raised (swallowed): %s", exc)
            benchmark = {}

        features = build_features(
            snapshots,
            bundle=bundle,
            benchmark=benchmark,
            benchmark_symbol="SPY",
        )

        regime_counts: dict[str, int] = {}
        for ticker, fs in features.items():
            regime_counts[fs.trend_regime] = regime_counts.get(fs.trend_regime, 0) + 1
            logger.info(
                "feature_set ticker=%s trend=%s momentum=%.2f vol=%s rs=%s "
                "rs_30d=%s quality=%.2f",
                ticker,
                fs.trend_regime,
                fs.momentum_score,
                fs.volatility_regime,
                fs.relative_strength_label,
                fs.relative_strength_30d,
                fs.data_quality_score,
            )
        logger.info(
            "feature_engine done — run=%s tickers=%d regimes=%s benchmark_return=%s",
            run_id, len(features), regime_counts,
            benchmark.get("pct_30d") if benchmark else None,
        )

        try:
            inserted = await asyncio.to_thread(
                persist_features,
                list(features.values()),
                run_id=run_id,
                user_id=str(self.user_id),
            )
            logger.info(
                "agent_features persisted — run=%s tickers=%d inserted=%d",
                run_id, len(features), inserted,
            )
        except Exception as exc:  # noqa: BLE001 — never fail a run on persistence
            logger.warning("agent_features persist raised (swallowed): %s", exc)

        return features

    # ── Portfolio synthesis stage (Phase 4) ─────────────────────────────────

    async def _run_portfolio_synthesis(
        self,
        *,
        context: dict[str, Any],
    ) -> PortfolioSynthesis:
        """Run the Phase 4 synthesis LLM call.

        Reads from the in-memory Phase 1-3 state (``self._snapshots``,
        ``self._features``, ``self._verdicts``) plus the macro snapshot
        from ``context``. Never raises — on LLM failure, falls back to
        :func:`deterministic_synthesis` which guarantees the Phase 4
        acceptance-gate minimums (≥2 themes, ≥1 risk concentration).
        """
        positions = context.get("portfolio") or []
        macro = context.get("macro") or {}
        snapshots = getattr(self, "_snapshots", {}) or {}
        features = getattr(self, "_features", {}) or {}
        verdicts = getattr(self, "_verdicts", {}) or {}
        decision: Optional[ModeDecision] = getattr(self, "_mode_decision", None)
        tracker: Optional[RunCostTracker] = getattr(self, "_cost_tracker", None)

        # Phase 5 — DEGRADED mode forces the deterministic synthesis
        # path. Zero LLM calls on the synthesis stage; the cost tracker
        # records zero and the projected savings calculation stays
        # accurate.
        llm_for_call = self._llm if self._llm.api_key else None
        if decision is not None and decision.mode == RunMode.DEGRADED:
            llm_for_call = None

        synthesis = await synthesize_portfolio(
            verdicts=verdicts,
            snapshots=snapshots,
            features=features,
            positions=positions,
            macro=macro,
            llm=llm_for_call,
        )

        # Append DEGRADED explanation to the summary so the UI sees it.
        if decision is not None and decision.mode == RunMode.DEGRADED and decision.explanation:
            degraded_tag = " " + decision.explanation
            if degraded_tag.strip() not in (synthesis.summary or ""):
                synthesis.summary = ((synthesis.summary or "")
                                     + degraded_tag)[:800]

        if tracker is not None and not synthesis.used_fallback:
            tracker.record(kind="synthesis", model=self._llm.model)

        logger.info(
            "portfolio_synthesis done — bias=%s themes=%d risks=%d "
            "overexposure=%d rebalance=%d fallback=%s mode=%s",
            synthesis.portfolio_bias,
            len(synthesis.key_themes),
            len(synthesis.risk_concentrations),
            len(synthesis.overexposure_flags),
            len(synthesis.rebalancing_suggestions),
            synthesis.used_fallback,
            decision.mode.value if decision else "unknown",
        )
        return synthesis

    # ── Per-ticker analyst stage (Phase 3) ──────────────────────────────────

    async def _run_per_ticker_analyst(self) -> dict[str, "AnalystVerdict"]:
        """Run the per-ticker analyst over the Phase 2 FeatureSets.

        Returns ``{ticker: AnalystVerdict}``. Every ticker is guaranteed
        to have an entry — unrecoverable failures produce an
        ``INSUFFICIENT_DATA`` verdict, never an empty map.

        Phase 5: when the run-mode decision is DEGRADED, the LLM stage
        is skipped entirely. Each ticker still gets a deterministic
        AnalystVerdict so downstream stages see the spec-mandated shape.
        """
        snapshots = getattr(self, "_snapshots", {}) or {}
        features = getattr(self, "_features", {}) or {}
        if not snapshots or not features:
            logger.info(
                "per-ticker analyst skipped — snapshots=%d features=%d",
                len(snapshots), len(features),
            )
            return {}

        decision: Optional[ModeDecision] = getattr(self, "_mode_decision", None)
        if decision is not None and decision.mode == RunMode.DEGRADED:
            verdicts = build_degraded_verdicts(snapshots, decision=decision)
            logger.info(
                "per-ticker analyst DEGRADED — tickers=%d reason=%s "
                "(0 LLM calls made)",
                len(verdicts), decision.reason,
            )
            return verdicts

        if not self._llm.api_key:
            logger.warning(
                "per-ticker analyst skipped — no anthropic_api_key; "
                "every ticker will carry an INSUFFICIENT_DATA verdict",
            )
            from ..intelligence import insufficient_data_verdict
            return {
                t: insufficient_data_verdict(t, error="no_api_key")
                for t in snapshots.keys()
            }

        verdicts = await analyze_portfolio(
            snapshots=snapshots,
            features=features,
            llm=self._llm,
            max_concurrency=3,
        )

        action_counts: dict[str, int] = {}
        fallback_count = 0
        tracker: Optional[RunCostTracker] = getattr(self, "_cost_tracker", None)
        for ticker, v in verdicts.items():
            action_counts[v.action] = action_counts.get(v.action, 0) + 1
            if v.used_fallback:
                fallback_count += 1
            # Record cost only for calls that actually hit the LLM. Bypass /
            # quality-gated fallbacks stay at zero cost.
            if tracker is not None and not v.used_fallback:
                tracker.record(kind="analyst", model=self._llm.model)
            logger.info(
                "analyst ticker=%s action=%s conviction=%.2f confidence=%.2f "
                "drivers=%d risks=%d fallback=%s",
                ticker, v.action, v.conviction, v.confidence,
                len(v.key_drivers), len(v.risks), v.used_fallback,
            )

        failure_rate = fallback_count / max(1, len(verdicts))
        logger.info(
            "per-ticker analyst done — tickers=%d actions=%s fallback_rate=%.2f",
            len(verdicts), action_counts, failure_rate,
        )
        return verdicts

    def _apply_verdicts_to_insights(
        self,
        state: AgentState,
        verdicts: dict[str, "AnalystVerdict"],
    ) -> None:
        """Project per-ticker verdicts onto the existing TickerInsight rows.

        Takes precedence over the monolithic Portfolio Agent output for
        action / conviction / thesis / drivers / risks — those fields
        are closer to the structured Phase 2 features. Falls back to
        whatever the monolithic call set when a verdict is missing.
        """
        if not verdicts:
            return

        for ticker, insight in state.insights.items():
            verdict = verdicts.get(ticker)
            if verdict is None:
                continue

            insight.suggested_action = action_to_suggested_action(verdict.action)
            # ``conviction_score`` stays in [-1, +1]; BUY/REDUCE map to
            # signed conviction so the downstream allocator preserves
            # direction. HOLD / INSUFFICIENT_DATA collapse to 0 so the
            # allocator never funnels cash into a neutral verdict.
            if verdict.action == "BUY":
                insight.conviction_score = verdict.conviction
            elif verdict.action == "REDUCE":
                insight.conviction_score = -verdict.conviction
            else:
                insight.conviction_score = 0.0

            # Compose the thesis: analyst drivers/risks first, then the
            # monolithic narrative (if any) for portfolio-level colour.
            existing = (insight.investment_thesis or "").strip()
            analyst_line = format_thesis(verdict)
            if existing and existing != analyst_line:
                insight.investment_thesis = (analyst_line + " " + existing)[:500]
            else:
                insight.investment_thesis = analyst_line

        # Re-run the cash allocator with the updated conviction scores
        # so BUY verdicts get deposit share and REDUCE verdicts don't.
        self._allocate_cash(state)

    # ── Persistence ─────────────────────────────────────────────────────────

    async def _persist(self, state: AgentState) -> None:
        await asyncio.to_thread(self._persist_sync, state)

    def _insert_insights_with_schema_fallback(
        self, insight_rows: list[dict[str, Any]],
    ) -> None:
        """Insert ``agent_insights`` rows, degrading on missing Phase 3 columns.

        When migration 010 hasn't been applied the insert raises with a
        schema-cache error naming the missing column; we strip the
        Phase-3-only fields and retry ONCE so deployments without the
        new columns keep persisting the core fields.
        """
        try:
            self.db.table("agent_insights").insert(insight_rows).execute()
            return
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            retryable = (
                "analyst_verdict" in msg
                or "analyst_confidence" in msg
                or "schema cache" in msg
                or "does not exist" in msg
                or "column" in msg
            )
            if not retryable:
                logger.warning("agent_insights insert failed: %s", exc)
                raise
            logger.warning(
                "agent_insights missing Phase 3 columns — retrying without "
                "analyst_verdict/analyst_confidence (apply migrations/"
                "010_analyst_verdict.sql to enable). err=%s", exc,
            )
        stripped = [
            {k: v for k, v in row.items()
             if k not in {"analyst_verdict", "analyst_confidence"}}
            for row in insight_rows
        ]
        self.db.table("agent_insights").insert(stripped).execute()

    def _persist_sync(self, state: AgentState) -> None:
        now = datetime.now(timezone.utc).isoformat()

        prev_insights: dict[str, dict] = {}
        tickers = list(state.insights.keys())
        if tickers:
            try:
                rows = (
                    self.db.table("agent_insights")
                    .select(
                        "ticker,suggested_action,sentiment_label,technical_signal,conviction_score,sentiment_score"
                    )
                    .eq("user_id", state.user_id)
                    .in_("ticker", tickers)
                    .order("created_at", desc=True)
                    .execute()
                ).data or []
                for row in rows:
                    if row["ticker"] not in prev_insights:
                        prev_insights[row["ticker"]] = row
            except Exception as exc:
                logger.warning("Failed to fetch previous insights for diff: %s", exc)

        what_changed_map: dict[str, str] = {}
        for ticker, insight in state.insights.items():
            prev = prev_insights.get(ticker)
            if prev:
                diff = _compute_what_changed(prev, insight)
                if diff:
                    what_changed_map[ticker] = diff

        verdicts = getattr(self, "_verdicts", {}) or {}
        insight_rows: list[dict[str, Any]] = []
        for insight in state.insights.values():
            row = insight.to_insight_row(run_id=state.run_id, user_id=state.user_id)
            wc = what_changed_map.get(insight.ticker)
            if wc:
                row["what_changed"] = wc
            # Phase 3 — attach the raw analyst verdict when available.
            # Missing column ``analyst_verdict`` is swallowed at insert
            # time by the fallback path below.
            verdict = verdicts.get(insight.ticker)
            if verdict is not None:
                row["analyst_verdict"] = verdict.to_dict()
                row["analyst_confidence"] = round(verdict.confidence, 2)
            insight_rows.append(row)

        if insight_rows:
            self._insert_insights_with_schema_fallback(insight_rows)

        rec_rows: list[dict[str, Any]] = []
        for insight in state.insights.values():
            action = insight.suggested_action or "HOLD"
            reasoning = insight.investment_thesis or f"{action} signal from portfolio agent."
            # Guarantee one recommendation row per ticker — the UI contract
            # mandates that every position gets a card (even degraded).
            # ``_apply_advice_to_insights`` now fills the thesis in every
            # branch, so no ticker is dropped at persist time.
            rec_rows.append({
                "user_id": state.user_id,
                "ticker": insight.ticker,
                "action": action,
                "detail": reasoning[:600],
                "rationale": self._rationale_line(insight),
                "urgency": self._urgency(insight),
                "tax_note": self._tax_note(insight),
                "drip_note": "",
                "is_active": True,
                "agent_run_id": state.run_id,
                "investment_thesis": insight.investment_thesis,
                "sentiment_score": _round(insight.sentiment_score),
                "technical_signal": insight.technical_signal,
                "conviction_score": _round(insight.conviction_score),
                "suggested_allocation": round(insight.suggested_allocation, 2),
                "what_changed": what_changed_map.get(insight.ticker),
            })

        if rec_rows:
            for i in range(0, len(rec_rows), 50):
                self.db.table("recommendations").insert(rec_rows[i:i + 50]).execute()

        try:
            self.db.table("recommendations").update({
                "is_active": False,
                "resolution": "expired",
                "resolved_at": now,
            }).eq("user_id", state.user_id).eq("is_active", True).neq(
                "agent_run_id", state.run_id
            ).execute()
            self.db.table("recommendations").update({
                "is_active": False,
                "resolution": "expired",
                "resolved_at": now,
            }).eq("user_id", state.user_id).eq("is_active", True).filter(
                "agent_run_id", "is", "null"
            ).execute()
        except Exception as exc:
            logger.warning("Failed to expire old recommendations: %s", exc)

    async def _update_run(
        self,
        run_id: str,
        *,
        status: Optional[str] = None,
        current_agent: Optional[str] = None,
        progress: Optional[int] = None,
        summary: Optional[str] = None,
        error_message: Optional[str] = None,
        allocation: Optional[dict] = None,
        synthesis: Optional[PortfolioSynthesis] = None,
        mode_decision: Optional[ModeDecision] = None,
        cost_tracker: Optional[RunCostTracker] = None,
    ) -> None:
        patch: dict = {}
        if status is not None:
            patch["status"] = status
        if current_agent is not None:
            patch["current_agent"] = current_agent
        if progress is not None:
            patch["progress_pct"] = progress
        if summary is not None:
            patch["summary"] = summary
        if error_message is not None:
            patch["error_message"] = error_message
        if allocation is not None:
            patch["allocation"] = allocation
        # Phase 4 — portfolio synthesis lives on agent_runs so the UI can
        # query one row per run without joining ``recommendations``.
        if synthesis is not None:
            patch["portfolio_synthesis"] = synthesis.to_dict()
            patch["synthesis_used_fallback"] = bool(synthesis.used_fallback)
        # Phase 5 — run-mode classification + cost metrics per run.
        if mode_decision is not None:
            patch["run_mode"] = mode_decision.mode.value
            patch["run_mode_decision"] = mode_decision.to_dict()
        if cost_tracker is not None:
            patch["cost_metrics"] = cost_tracker.to_dict()
        if status in ("completed", "failed"):
            patch["finished_at"] = datetime.now(timezone.utc).isoformat()
        if not patch:
            return
        self._run_agent_runs_update(run_id, patch)

    def _run_agent_runs_update(self, run_id: str, patch: dict) -> None:
        """Execute the ``agent_runs`` update with Phase 4 + 5 column fallbacks.

        Each Phase's new columns can independently be missing on older
        deployments. On a schema-cache / missing-column error, drop the
        Phase-5 columns first, then Phase-4, retrying at most twice so
        the core fields always land.
        """
        try:
            self.db.table("agent_runs").update(patch).eq("id", run_id).execute()
            return
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            schema_error = (
                "schema cache" in msg or "does not exist" in msg
                or "column" in msg
            )
            if not schema_error:
                logger.warning("agent_runs update failed: %s", exc)
                return

        phase5_cols = {"run_mode", "run_mode_decision", "cost_metrics"}
        phase4_cols = {"portfolio_synthesis", "synthesis_used_fallback"}

        if any(c in patch for c in phase5_cols):
            logger.warning(
                "agent_runs missing Phase 5 columns — retrying without "
                "run_mode/cost_metrics (apply migrations/"
                "012_run_mode_cost.sql).",
            )
            stripped = {k: v for k, v in patch.items() if k not in phase5_cols}
            try:
                self.db.table("agent_runs").update(stripped).eq("id", run_id).execute()
                return
            except Exception:  # noqa: BLE001 — fall through to Phase-4 strip
                pass

        if any(c in patch for c in phase4_cols):
            logger.warning(
                "agent_runs missing Phase 4 columns — retrying without "
                "portfolio_synthesis (apply migrations/"
                "011_portfolio_synthesis.sql).",
            )
            stripped = {
                k: v for k, v in patch.items()
                if k not in (phase4_cols | phase5_cols)
            }
            try:
                self.db.table("agent_runs").update(stripped).eq("id", run_id).execute()
                return
            except Exception as exc2:  # noqa: BLE001
                logger.warning("agent_runs update retry failed: %s", exc2)

    # ── Mappers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _rationale_line(insight: TickerInsight) -> str:
        parts = []
        if insight.sentiment_label:
            parts.append(
                f"sent {insight.sentiment_label}"
                + (f"({insight.sentiment_score})" if insight.sentiment_score is not None else "")
            )
        if insight.technical_signal:
            parts.append(f"tech {insight.technical_signal}")
        if insight.fundamental_score is not None:
            parts.append(f"fund {insight.fundamental_score:+.2f}")
        if insight.conviction_score is not None:
            parts.append(f"conviction {insight.conviction_score:+.2f}")
        if insight.suggested_allocation > 0:
            parts.append(f"alloc ${insight.suggested_allocation:.0f}")
        return " · ".join(parts)

    @staticmethod
    def _urgency(insight: TickerInsight) -> int:
        c = insight.conviction_score or 0
        if insight.suggested_action == "SELL":
            return 4
        if abs(c) >= 0.60:
            return 3
        if abs(c) >= 0.35:
            return 2
        if abs(c) >= 0.15:
            return 1
        return 0

    @staticmethod
    def _tax_note(insight: TickerInsight) -> str:
        if insight.suggested_action in ("SELL", "TRIM") and insight.shares > 0:
            return (
                "LT eligible — 15-20% cap gains"
                if insight.lt_eligible
                else "ST status — 37% ordinary income tax"
            )
        return ""


# ── Helpers ────────────────────────────────────────────────────────────────


def _round(v):
    return round(v, 2) if v is not None else None


def _clamp(v: Any, lo: float, hi: float) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN guard
        return None
    return max(lo, min(hi, f))


def _to_float_or_none(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:
        return None
    return f


# ── Confidence gating helpers ───────────────────────────────────────────────


def _confidence_cap_for(completeness: float) -> float:
    """Map a ``data_completeness_score`` to the max |conviction| allowed.

    Rules (per v3 stability spec):
      * completeness < 0.4 → 0.3 (watchlist-tier)
      * completeness 0.4–0.7 → 0.6 (partial signal)
      * completeness > 0.7 → 1.0 (full signal, no clamp)
    """
    try:
        c = float(completeness)
    except (TypeError, ValueError):
        return 0.3
    if c != c:  # NaN guard
        return 0.3
    if c < 0.4:
        return 0.3
    if c <= 0.7:
        return 0.6
    return 1.0


def _apply_cap(conviction: Optional[float], cap: float) -> Optional[float]:
    """Clamp |conviction| to ``cap`` while preserving sign. Returns None if input is None."""
    if conviction is None:
        return None
    if cap <= 0:
        return 0.0
    if conviction > cap:
        return cap
    if conviction < -cap:
        return -cap
    return conviction


def _extract_confidence_from_context(context: dict[str, Any]) -> dict[str, float]:
    """Return ``{ticker: data_completeness_score}`` from the outgoing LLM context.

    Uses the context we JUST built so the cap enforced after the LLM call
    matches exactly what the LLM was told — no chance of drift between the
    prompt contract and the deterministic enforcement layer.
    """
    out: dict[str, float] = {}
    for entry in (context.get("portfolio") or []):
        t = (entry.get("ticker") or "").upper()
        if not t:
            continue
        score = entry.get("data_completeness_score")
        if score is None:
            score = entry.get("confidence_score")
        try:
            out[t] = float(score) if score is not None else 1.0
        except (TypeError, ValueError):
            out[t] = 1.0
    return out


def _force_lightweight_caps(context: dict[str, Any]) -> None:
    """LIGHTWEIGHT override: clamp every ticker's confidence cap to 0.3.

    Applied BEFORE ``_inject_confidence_caps`` runs in the LIGHTWEIGHT code
    path so the per-ticker caps are visible to the LLM in addition to the
    appended prompt rules. Also flags ``fallbacks_used`` on the
    portfolio-level ``data_quality`` block so the UI layer can badge the
    cards appropriately.
    """
    portfolio = context.get("portfolio") or []
    for entry in portfolio:
        # Floor the per-ticker completeness so ``_inject_confidence_caps``
        # (called immediately after us) derives cap = 0.3. Doing it via
        # ``data_completeness_score`` instead of writing ``confidence_cap``
        # directly keeps the two fields in lock-step.
        current = entry.get("data_completeness_score")
        try:
            current_f = float(current) if current is not None else 1.0
        except (TypeError, ValueError):
            current_f = 1.0
        entry["data_completeness_score"] = min(current_f, 0.39)
    dq = context.setdefault("data_quality", {})
    dq["fallbacks_used"] = True
    existing_missing = list(dq.get("missing_fields") or [])
    if "system_mode=LIGHTWEIGHT" not in existing_missing:
        existing_missing.append("system_mode=LIGHTWEIGHT")
    dq["missing_fields"] = existing_missing


def _inject_confidence_caps(context: dict[str, Any]) -> None:
    """Mutate ``context`` in place: add ``confidence_cap`` + per-ticker
    completeness mirrors to every portfolio entry.

    The context builder already produces ``confidence_score`` per ticker —
    we promote that to the explicit ``data_completeness_score`` / ``missing_
    fields`` / ``confidence_cap`` triple the LLM prompt references, so the
    prompt contract and the payload stay in lock-step.
    """
    portfolio = context.get("portfolio") or []
    portfolio_missing = ((context.get("data_quality") or {}).get("missing_fields") or [])
    for entry in portfolio:
        score = entry.get("data_completeness_score")
        if score is None:
            score = entry.get("confidence_score", 1.0)
        try:
            score_f = float(score)
        except (TypeError, ValueError):
            score_f = 1.0
        entry["data_completeness_score"] = round(score_f, 3)
        # Promote the per-ticker missing-field list up one level so it's
        # visible alongside the cap without the LLM having to drill into
        # ``data_quality``.
        dq = entry.get("data_quality") or {}
        entry["missing_fields"] = list(
            dq.get("missing_fields") or portfolio_missing or []
        )
        entry["confidence_cap"] = _confidence_cap_for(score_f)


# ── Deterministic recommendation fallback ─────────────────────────────────────


def _generate_deterministic_recs(context: dict[str, Any]) -> dict[str, Any]:
    """Build a recommendation response from price-trend signals — no LLM needed.

    Called when completeness < 0.6, api_key missing, or LLM fails after retry.
    Guarantees one card per ticker with varied, ticker-specific reasoning.
    Never returns {}.
    """
    portfolio = context.get("portfolio") or []
    data_quality = context.get("data_quality") or {}
    completeness = float(data_quality.get("completeness_score") or 0.0)

    cards: list[dict[str, Any]] = []
    top_buys: list[str] = []

    for entry in portfolio:
        ticker = (entry.get("ticker") or "").upper()
        if not ticker:
            continue

        trend = (entry.get("trend") or "flat").lower()
        sentiment = (entry.get("sentiment_label") or "neutral").lower()
        tech = (entry.get("technical_signal") or "NEUTRAL").upper()
        cap = float(entry.get("confidence_cap") or 0.3)
        category = entry.get("category") or "Other"
        missing = list(entry.get("missing_fields") or [])
        fund_score = entry.get("fundamental_score")

        action, conviction, confidence_label = _map_signals_to_action(
            trend=trend, sentiment=sentiment, technical=tech, cap=cap,
        )
        thesis = _deterministic_thesis(
            ticker=ticker, action=action, trend=trend, sentiment=sentiment,
            missing=missing, category=category, completeness=completeness,
            fund_score=fund_score,
        )

        cards.append({
            "ticker": ticker,
            "action": action,
            "confidence": confidence_label,
            "conviction": round(conviction, 2),
            "sentiment_label": sentiment,
            "sentiment_score": entry.get("sentiment_score"),
            "technical_signal": tech,
            "fundamental_score": fund_score,
            "thesis": thesis,
            "reasoning": thesis,
        })
        if action == "BUY":
            top_buys.append(ticker)

    buy_count = sum(1 for c in cards if c["action"] == "BUY")
    hold_count = len(cards) - buy_count
    summary = (
        f"Limited market data (confidence {completeness:.0%}) — recommendations "
        f"derived from price-trend signals only. "
        f"{buy_count} BUY, {hold_count} HOLD signals detected. "
        "Refresh with better connectivity for full AI analysis."
    )
    return {
        "summary": summary,
        "risks": [
            "Data completeness below confidence threshold",
            "Recommendations based on partial signals only",
        ],
        "opportunities": [f"Trend signal: {t}" for t in top_buys[:3]],
        "top_buys": top_buys[:3],
        "cards": cards,
        "_fallback": True,
        "_reason": "low_data_confidence",
    }


def _map_signals_to_action(
    *,
    trend: str,
    sentiment: str,
    technical: str,
    cap: float,
) -> tuple[str, float, str]:
    """Map trend + sentiment + technical to (action, conviction, confidence_label).

    Conservative: only BUY on clear positive confluence; default HOLD otherwise
    since we're operating with low data confidence.
    """
    score = 0.0
    if trend == "up":
        score += 0.4
    elif trend == "down":
        score -= 0.4
    if sentiment == "bullish":
        score += 0.3
    elif sentiment == "bearish":
        score -= 0.3
    if technical == "BUY":
        score += 0.2
    elif technical == "SELL":
        score -= 0.2

    if score >= 0.5:
        return "BUY", min(cap, 0.25), "low"
    # Never emit SELL on low data — default HOLD with zero conviction.
    return "HOLD", 0.0, "low"


def _deterministic_thesis(
    *,
    ticker: str,
    action: str,
    trend: str,
    sentiment: str,
    missing: list[str],
    category: str,
    completeness: float,
    fund_score: Any = None,
) -> str:
    """Produce a per-ticker thesis string from available signals.

    Varies by trend, sentiment, category, and missing-field pattern so no two
    cards ever share identical text — even when all tickers get HOLD.
    """
    trend_phrases = {
        "up":   f"{ticker} is trending upward",
        "down": f"{ticker} has shown recent price weakness",
        "flat": f"{ticker} is trading in a flat range",
    }
    s1_base = trend_phrases.get(trend, f"{ticker} trend is inconclusive")
    sentiment_adj = {
        "bullish": "with positive market sentiment",
        "bearish": "against cautious market sentiment",
        "neutral": "with neutral market conditions",
    }
    s1 = f"{s1_base} {sentiment_adj.get(sentiment, '')}".strip() + "."

    gap_count = len(missing)
    if gap_count >= 3:
        gap_note = "most signal sources unavailable"
    elif gap_count > 0:
        gap_note = f"{', '.join(missing[:2])} data missing"
    else:
        gap_note = "limited data sources active"

    try:
        fs = float(fund_score) if fund_score is not None else None
    except (TypeError, ValueError):
        fs = None

    if action == "BUY" and category == "Crypto":
        s2 = (
            f"Trend signals support cautious accumulation ({gap_note}) — "
            "low data confidence; size conservatively."
        )
    elif action == "BUY":
        s2 = (
            f"Upward trend suggests monitored accumulation ({gap_note}) — "
            "verify with fresh data before increasing position."
        )
    elif fs is not None and fs < -0.2:
        s2 = (
            f"Fundamentals signal caution (score {fs:+.2f}) and {gap_note} — "
            "holding pending clearer directional data."
        )
    else:
        s2 = (
            f"No strong directional signal detected ({gap_note}) — "
            "maintaining HOLD as watchlist position."
        )
    return f"{s1} {s2}"


def _build_simplified_prompt(context: dict[str, Any]) -> str:
    """Strip heavy enrichment fields for the LLM retry — reduces tokens ~60%.

    Keeps only what the prompt contract requires: ticker, trend, signals,
    confidence caps, and data-quality metadata.
    """
    portfolio = context.get("portfolio") or []
    slim_portfolio = [
        {
            "ticker": p.get("ticker"),
            "trend": p.get("trend"),
            "sentiment_label": p.get("sentiment_label"),
            "technical_signal": p.get("technical_signal"),
            "fundamental_score": p.get("fundamental_score"),
            "confidence_cap": p.get("confidence_cap"),
            "data_completeness_score": p.get("data_completeness_score"),
            "missing_fields": p.get("missing_fields"),
        }
        for p in portfolio
    ]
    return json.dumps(
        {
            "portfolio": slim_portfolio,
            "data_quality": context.get("data_quality"),
            "macro": context.get("macro"),
        },
        default=str,
    )


def _normalize_action(action: Any) -> str:
    if not action:
        return "HOLD"
    a = str(action).upper().strip()
    if a in {"BUY", "SELL", "HOLD", "TRIM", "REVIEW", "ACCUMULATE", "DCA"}:
        if a in {"ACCUMULATE", "DCA"}:
            return "BUY"
        return a
    return "HOLD"


def _compute_what_changed(prev: dict, curr: "TickerInsight") -> str:
    bullets: list[str] = []

    prev_action = prev.get("suggested_action")
    if prev_action and prev_action != curr.suggested_action:
        bullets.append(f"Action: {prev_action} → {curr.suggested_action}")

    prev_sent = prev.get("sentiment_label")
    if prev_sent and prev_sent != curr.sentiment_label and curr.sentiment_label:
        bullets.append(f"Sentiment: {prev_sent} → {curr.sentiment_label}")

    prev_tech = prev.get("technical_signal")
    if prev_tech and prev_tech != curr.technical_signal and curr.technical_signal:
        bullets.append(f"Technical: {prev_tech} → {curr.technical_signal}")

    prev_conv = prev.get("conviction_score")
    curr_conv = curr.conviction_score
    if prev_conv is not None and curr_conv is not None:
        delta = curr_conv - float(prev_conv)
        if abs(delta) >= 0.10:
            sign = "+" if delta >= 0 else ""
            bullets.append(
                f"Conviction: {float(prev_conv):+.2f} → {curr_conv:+.2f} ({sign}{delta:.2f})"
            )

    prev_ss = prev.get("sentiment_score")
    curr_ss = curr.sentiment_score
    if prev_ss is not None and curr_ss is not None:
        delta = curr_ss - float(prev_ss)
        if abs(delta) >= 0.15:
            sign = "+" if delta >= 0 else ""
            bullets.append(
                f"Sentiment score: {float(prev_ss):+.2f} → {curr_ss:+.2f} ({sign}{delta:.2f})"
            )

    return "\n".join(bullets)
