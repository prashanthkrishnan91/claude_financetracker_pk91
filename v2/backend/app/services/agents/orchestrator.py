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
from .llm import LLMClient
from .state import AgentState, TickerInsight

logger = logging.getLogger(__name__)


# Module-level lock: enforces a single LLM call in flight per process for the
# orchestration pipeline. Retries live at the HTTP layer inside LLMClient, not
# at the orchestration layer.
LLM_SEMAPHORE = asyncio.Semaphore(1)


PORTFOLIO_AGENT_CONTRACT = """You are the Portfolio Agent for a long-term retail investor.

You receive a JSON object with these keys:
  - "portfolio": array of position objects (ticker, shares, avg_cost,
    current_price, category, sentiment_label, technical_signal,
    fundamental_score, trend, confidence_score, confidence_label,
    data_quality, what_changed). Every ticker is GUARANTEED to have a
    value for each signal field — missing upstream data has already been
    filled with deterministic fallbacks and the gaps are recorded in
    `data_quality.missing_fields`.
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

Analyse the full portfolio as a whole — you do NOT get a second call. For
each ticker decide BUY / HOLD / SELL with a confidence (high/medium/low),
a conviction score in [-1.0, +1.0], and a 2-sentence reasoning that cites
sentiment / technical / fundamental context where available. For tickers
whose `confidence_label` is "watchlist only" or "low confidence signal",
default to HOLD and note the data gap in the thesis — NEVER emit the
string "insufficient data" in any field.

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
        # Hard guarantee: exactly one LLM call per orchestrator run. Asserted
        # inside ``_single_llm_call`` and logged on ``run`` completion.
        self._llm_call_count = 0

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
            live_prices = await self._fetch_live_prices_for_user()
            timings["fetch_live_prices_ms"] = round((time.perf_counter() - t0) * 1000, 1)

            await self._update_run(run_id, current_agent="Building context", progress=25)
            t0 = time.perf_counter()
            context = build_portfolio_context(
                user_id=str(self.user_id),
                live_prices=live_prices,
            )
            timings["build_context_ms"] = round((time.perf_counter() - t0) * 1000, 1)

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
                run_id, current_agent="Portfolio Agent", progress=60
            )

            t0 = time.perf_counter()
            advice = await self._single_llm_call(context)
            timings["llm_call_ms"] = round((time.perf_counter() - t0) * 1000, 1)

            state.portfolio_advice = advice or {}
            state.pm_summary = (advice or {}).get("summary", "")

            self._apply_advice_to_insights(state, advice or {})

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
            )
            timings["total_ms"] = round((time.perf_counter() - run_start) * 1000, 1)
            logger.info(
                "Agent run completed — id=%s insights=%d llm_calls=%d timings=%s",
                run_id, len(state.insights), self._llm_call_count, timings,
            )
            # Hard assertion: the orchestrator must never invoke the LLM
            # more than once per run. The semaphore + this counter are a
            # belt-and-suspenders guarantee against future regressions.
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
        """Execute the one-and-only Claude call for this run.

        Serialised behind ``LLM_SEMAPHORE``. Retries live inside ``LLMClient``
        at the HTTP level — the orchestration layer never re-enters this
        function. ``self._llm_call_count`` is incremented atomically so the
        ``run`` epilogue can detect any future regression that re-invokes
        this method from within a single run.
        """
        if not self._llm.api_key:
            logger.warning("Skipping LLM call — no anthropic_api_key configured")
            return {}

        # Reinforce the semaphore contract with an in-instance counter —
        # the semaphore protects the process; the counter protects this run.
        self._llm_call_count += 1
        if self._llm_call_count > 1:
            logger.error(
                "Orchestrator attempted a second LLM call within one run — blocked"
            )
            return {}

        user_message = json.dumps(context, default=str)
        async with LLM_SEMAPHORE:
            logger.info(
                "LLM call start — model=%s tickers=%d",
                self._llm.model,
                len(context.get("portfolio") or []),
            )
            response = await self._llm.ask_json(
                system=PORTFOLIO_AGENT_CONTRACT,
                user=user_message,
                max_tokens=3500,
            )
        return response or {}

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
        """Project the single LLM response onto per-ticker TickerInsight rows."""
        cards = advice.get("cards") or []
        card_map: dict[str, dict[str, Any]] = {}
        for card in cards:
            t = (card.get("ticker") or "").upper()
            if t:
                card_map[t] = card

        for ticker, insight in state.insights.items():
            card = card_map.get(ticker.upper())
            if not card:
                # No per-ticker guidance → safe HOLD default. Never surface
                # "insufficient data" — UI contract mandates one of:
                # high confidence / partial signal / low confidence signal /
                # watchlist only.
                insight.suggested_action = insight.suggested_action or "HOLD"
                if not insight.investment_thesis:
                    insight.investment_thesis = (
                        f"{ticker}: partial signal from portfolio agent — "
                        "holding position pending richer data."
                    )
                continue

            insight.suggested_action = _normalize_action(card.get("action"))
            insight.conviction_score = _clamp(card.get("conviction"), -1.0, 1.0)
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
                # The LLM returned a card but skipped the thesis. Fill a
                # deterministic explanation so the UI card is never blank.
                thesis = (
                    f"{ticker}: {insight.suggested_action} — "
                    "portfolio agent signal without a detailed rationale."
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

    # ── Live price bootstrap (cache-first via io_layer) ──────────────────────

    async def _fetch_live_prices_for_user(self) -> dict[str, float]:
        """Return ``{ticker: mid_price}`` for the user's positions.

        Routed through the shared market cache so repeated requests for the
        same ticker within the TTL window collapse to a single upstream call.
        Failures are isolated — any broken upstream returns an empty dict, the
        context builder still produces a usable LLM prompt, and the pipeline
        is never retried.
        """
        if not self._price_service:
            return {}
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
            logger.warning("Failed to list tickers for price fetch: %s", exc)
            return {}
        if not tickers:
            return {}
        try:
            bundle = await io_layer.fetch_market_bundle(
                tickers,
                price_service=self._price_service,
                finnhub_key=self._finnhub_key,
                polygon_key=self._polygon_key,
            )
            return dict(bundle.get("live_prices") or {})
        except Exception as exc:  # noqa: BLE001 — absolute failure isolation
            logger.warning("io_layer price fetch failed, degrading gracefully: %s", exc)
            return {}

    # ── Persistence ─────────────────────────────────────────────────────────

    async def _persist(self, state: AgentState) -> None:
        await asyncio.to_thread(self._persist_sync, state)

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

        insight_rows: list[dict[str, Any]] = []
        for insight in state.insights.values():
            row = insight.to_insight_row(run_id=state.run_id, user_id=state.user_id)
            wc = what_changed_map.get(insight.ticker)
            if wc:
                row["what_changed"] = wc
            insight_rows.append(row)

        if insight_rows:
            self.db.table("agent_insights").insert(insight_rows).execute()

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
        if status in ("completed", "failed"):
            patch["finished_at"] = datetime.now(timezone.utc).isoformat()
        if not patch:
            return
        try:
            self.db.table("agent_runs").update(patch).eq("id", run_id).execute()
        except Exception as exc:
            logger.warning("agent_runs update failed: %s", exc)

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
