"""Hand-rolled async orchestrator for the multi-agent trading pipeline.

No LangGraph. Just asyncio.gather with status hooks that write progress to
the `agent_runs` table so the UI can poll for live updates.

Pipeline phases:
  1. bootstrap         — load positions, prices, build AgentState
  2. sentiment         — fan out sentiment agent per ticker
  3. technical         — fan out technical agent per ticker
  4. fundamental       — fan out fundamental agent per ticker
  5. portfolio_mgr     — single LLM call, deposit allocation
  5.5. portfolio_advisor — comprehensive portfolio analysis via LLM
  6. persist           — write agent_insights + recommendations rows

Phases 2-4 run sequentially (not parallel) so the UI progress bar shows
meaningful states. Within a phase, tickers are processed concurrently with
a small semaphore to respect Anthropic / Finnhub rate limits.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional
from uuid import UUID, uuid4

from ...database import get_supabase_client
from .data_sources import _get_client as _build_http_client
from .fundamental_agent import run_fundamental_agent
from .llm import LLMClient
from .portfolio_manager import run_portfolio_manager
from .sentiment_agent import run_sentiment_agent
from .state import AgentState, TickerInsight
from .technical_agent import run_technical_agent

logger = logging.getLogger(__name__)


# Bounded concurrency for the fan-out agents. Tuned for free-tier API limits.
_AGENT_CONCURRENCY = 6


@dataclass
class AgentPipelineResult:
    run_id: str
    status: str
    summary: str
    insights: list[TickerInsight]


class AgentOrchestrator:
    """Drives a single agent run end-to-end.

    Status updates write to the `agent_runs` table so that the FastAPI
    /recommendations/jobs/{id} endpoint can surface progress.
    """

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

    # ── Public API ────────────────────────────────────────────────────────────

    async def create_run(self, tickers: Optional[list[str]] = None) -> str:
        """Insert a queued agent_runs row and return the job_id.

        The UI calls this synchronously via the router; the actual pipeline
        is dispatched via FastAPI BackgroundTasks.
        """
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
        """Execute the full pipeline for a given run_id.

        Guaranteed lifecycle: NEVER leaves job in 'running' state.
        - status='running' at start
        - ALWAYS transitions to 'completed' or 'failed' before return
        - Finally block ensures status update even on unexpected crash
        """
        terminal_status_set = False
        try:
            logger.info("Agent run started — id=%s user=%s", run_id, self.user_id)
            logger.info("Using model: %s (fallback: %s)", self._llm.model, self._llm.fallback_model)
            await self._update_run(run_id, status="running", current_agent="Loading portfolio", progress=5)

            state = await self._bootstrap(run_id)
            if not state.insights:
                await self._update_run(
                    run_id,
                    status="completed",
                    current_agent="No positions",
                    progress=100,
                    summary="No positions in portfolio; nothing to analyse.",
                )
                logger.info("Agent run completed (no positions) — id=%s", run_id)
                terminal_status_set = True
                return AgentPipelineResult(run_id=run_id, status="completed",
                                           summary="No positions.", insights=[])

            async with await _build_http_client() as http_client:
                # Phases 2-4: run all three analysis phases concurrently.
                # Each agent writes to different fields on TickerInsight so
                # concurrent access is safe. ~3x faster than sequential baseline.
                await self._update_run(run_id, current_agent="Analyzing portfolio", progress=20)
                _fk = self._finnhub_key
                _pk = self._polygon_key
                _llm = self._llm
                await asyncio.gather(
                    self._fanout(state, lambda i: run_sentiment_agent(i, _llm, http_client, _fk)),
                    self._fanout(state, lambda i: run_technical_agent(i, _llm, http_client, _pk)),
                    self._fanout(state, lambda i: run_fundamental_agent(i, _llm, http_client)),
                )

                # Phase 5: Portfolio Manager synthesis
                await self._update_run(run_id, current_agent="Portfolio Manager Deliberating", progress=85)
                logger.info("Calling LLM for portfolio manager (run %s)", run_id)
                await run_portfolio_manager(state, self._llm)
                if not state.pm_summary:
                    logger.warning("Portfolio manager returned empty summary for run %s — LLM key may be missing", run_id)
                    await self._update_run(run_id, error_message="LLM returned empty summary; check Anthropic API key.")

                # Phase 5.5: Portfolio Advisor (comprehensive portfolio analysis)
                await self._update_run(run_id, current_agent="Portfolio Advisor", progress=90)
                logger.info("Calling portfolio advisor for run %s", run_id)
                await self._call_portfolio_advisor(state)

            # Phase 6: Persist results
            await self._update_run(run_id, current_agent="Saving Insights", progress=95)
            logger.info("Saving result for run %s (%d insights)", run_id, len(state.insights))
            await self._persist(state)

            # Build allocation map for the run row
            allocation_map = {
                i.ticker: i.suggested_allocation
                for i in state.insights.values()
                if i.suggested_allocation > 0
            }
            # Guarantee the run row always has a human-readable summary so
            # the UI never renders a blank Intel panel on completion.
            # Prefer portfolio advisor summary if available
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
            logger.info("Agent run completed — id=%s status=completed insights=%d", run_id, len(state.insights))
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
            logger.info("Agent run completed — id=%s status=failed error=%s", run_id, str(exc)[:100])
            terminal_status_set = True
            return AgentPipelineResult(
                run_id=run_id,
                status="failed",
                summary=fallback_summary,
                insights=[],
            )

        finally:
            # Guarantee terminal state — if exception bypassed both paths above,
            # mark job as failed. Should never happen, but defense-in-depth.
            if not terminal_status_set:
                logger.warning("LIFECYCLE VIOLATION: run %s did not reach terminal state — forcing failed", run_id)
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
                    logger.warning("Failed to mark run %s failed in finally block: %s", run_id, cleanup_exc)

    # ── Internal phases ───────────────────────────────────────────────────────

    async def _bootstrap(self, run_id: str) -> AgentState:
        """Load positions + live prices and build the per-ticker AgentState."""
        positions = (
            self.db.table("positions")
            .select("*")
            .eq("user_id", str(self.user_id))
            .execute()
        ).data or []

        state = AgentState(
            user_id=str(self.user_id),
            run_id=run_id,
            tickers=[p["ticker"] for p in positions],
            deposit_amount=self.deposit_amount,
            sale_proceeds=self.sale_proceeds,
        )

        if not positions:
            return state

        # Live prices (best-effort)
        prices: dict[str, float] = {}
        if self._price_service:
            try:
                results = await self._price_service.fetch_prices(
                    [p["ticker"] for p in positions]
                )
                for t, pr in results.items():
                    if pr.is_valid:
                        prices[t] = pr.mid_price
            except Exception as exc:
                logger.warning("Price fetch failed during bootstrap: %s", exc)

        # Compute totals and build insights
        total = 0.0
        category_totals: dict[str, float] = {}
        rows: list[tuple[dict, float, float]] = []
        for p in positions:
            shares = float(p.get("shares") or 0)
            avg_cost = float(p.get("avg_cost") or 0)
            price = prices.get(p["ticker"], avg_cost)
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
            shares = float(p.get("shares") or 0)
            avg_cost = float(p.get("avg_cost") or 0)
            pnl_pct = None
            if avg_cost > 0 and price:
                pnl_pct = round((price - avg_cost) / avg_cost * 100, 2)
            weight = round((mv / total * 100) if total > 0 else 0, 2)
            insight = TickerInsight(
                ticker=p["ticker"],
                name=p.get("name", p["ticker"]),
                category=p.get("category", "Other"),
                shares=shares,
                avg_cost=avg_cost,
                current_price=price,
                current_weight_pct=weight,
                pnl_pct=pnl_pct,
                lt_eligible=bool(p.get("lt_eligible", False)),
                target_price=float(p["target_price"]) if p.get("target_price") else None,
            )
            state.insights[insight.ticker] = insight

        return state

    async def _fanout(
        self,
        state: AgentState,
        coro_factory: Callable[[TickerInsight], Awaitable[None]],
    ) -> None:
        """Run `coro_factory(insight)` for each ticker with bounded concurrency.

        Each agent call is capped at 8 s so a single slow ticker cannot stall
        the whole phase when phases run concurrently.
        """
        sem = asyncio.Semaphore(_AGENT_CONCURRENCY)

        async def _wrap(insight: TickerInsight):
            async with sem:
                try:
                    await asyncio.wait_for(coro_factory(insight), timeout=8.0)
                except asyncio.TimeoutError:
                    logger.warning("Agent timed out for %s: skipping", insight.ticker)
                except Exception as exc:
                    logger.warning("Agent failed for %s: %s", insight.ticker, exc)

        await asyncio.gather(*[_wrap(i) for i in state.insights.values()])

    async def _call_portfolio_advisor(self, state: AgentState) -> None:
        """Call the portfolio advisor LLM with current state.

        Builds portfolio_positions from insights and generates a macro_summary,
        then calls portfolio_advisor. Result stored in state.portfolio_advice.
        """
        from ..recommendation_engine import portfolio_advisor

        try:
            # Build portfolio positions from current insights (post-agent enrichment)
            portfolio_positions = []
            for insight in state.insights.values():
                pos = {
                    "ticker": insight.ticker,
                    "shares": insight.shares,
                    "current_price": insight.current_price,
                    "avg_cost": insight.avg_cost,
                    "pnl_pct": insight.pnl_pct,
                    "category": insight.category,
                    "weight_pct": insight.current_weight_pct,
                    "sentiment_label": insight.sentiment_label,
                    "technical_signal": insight.technical_signal,
                    "suggested_action": insight.suggested_action,
                    "conviction_score": round(insight.conviction_score, 2) if insight.conviction_score is not None else None,
                }
                portfolio_positions.append(pos)

            # Build macro summary from state + agent insights
            portfolio_value = f"${state.total_portfolio_value:,.2f}"
            categories = " / ".join([
                f"{k}: {v:.1f}%"
                for k, v in sorted(state.category_weights.items())
            ])
            bullish_count = sum(
                1 for i in state.insights.values()
                if i.suggested_action in ("BUY", "ACCUMULATE", "DCA")
            )
            macro_summary = (
                f"Portfolio: {portfolio_value} · Allocation: {categories} · "
                f"Cash to deploy: ${state.cash_to_deploy:,.2f} · "
                f"Bullish signals: {bullish_count}/{len(state.insights)}"
            )

            # Call portfolio advisor
            advice = await portfolio_advisor(
                portfolio_positions=portfolio_positions,
                macro_summary=macro_summary,
                api_key=self._llm.api_key,
            )
            state.portfolio_advice = advice
            logger.info("Portfolio advisor returned advice for run %s", state.run_id)
        except Exception as exc:
            logger.warning("Portfolio advisor failed for run %s: %s", state.run_id, exc)
            state.portfolio_advice = {}  # Safe default

    async def _persist(self, state: AgentState) -> None:
        """Write agent_insights + refresh the recommendations table.

        Runs in a thread so synchronous Supabase calls don't block the event loop.
        Inserts new recommendations BEFORE deactivating old ones to prevent a
        blank-state window if the deactivation call fails.
        """
        await asyncio.to_thread(self._persist_sync, state)

    def _persist_sync(self, state: AgentState) -> None:
        now = datetime.now(timezone.utc).isoformat()

        # 0. Fetch previous insights per ticker for diff computation
        prev_insights: dict[str, dict] = {}
        tickers = list(state.insights.keys())
        if tickers:
            try:
                rows = (
                    self.db.table("agent_insights")
                    .select("ticker,suggested_action,sentiment_label,technical_signal,conviction_score,sentiment_score")
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

        # Build per-ticker what_changed strings
        what_changed_map: dict[str, str] = {}
        for ticker, insight in state.insights.items():
            prev = prev_insights.get(ticker)
            if prev:
                diff = _compute_what_changed(prev, insight)
                if diff:
                    what_changed_map[ticker] = diff

        # 1. Insert agent_insights — errors propagate to mark the run as failed
        insight_rows = []
        for insight in state.insights.values():
            row = insight.to_insight_row(run_id=state.run_id, user_id=state.user_id)
            wc = what_changed_map.get(insight.ticker)
            if wc:
                row["what_changed"] = wc
            insight_rows.append(row)

        if insight_rows:
            self.db.table("agent_insights").insert(insight_rows).execute()

        # 2. Build fresh recommendation rows
        # Map portfolio advice cards by ticker for lookup
        advice_cards_map: dict[str, dict] = {}
        if state.portfolio_advice and state.portfolio_advice.get("cards"):
            for card in state.portfolio_advice["cards"]:
                advice_cards_map[card.get("ticker", "")] = card

        rec_rows = []
        for insight in state.insights.values():
            # Get portfolio advisor's card for this ticker if available
            advice_card = advice_cards_map.get(insight.ticker)

            # Use portfolio advisor action if available, otherwise use insight action
            action = insight.suggested_action
            reasoning = ""
            if advice_card:
                action = advice_card.get("action", action)
                reasoning = advice_card.get("reasoning", "")

            if not insight.investment_thesis and action == "HOLD" and not reasoning:
                # Keep the table slim — skip pure HOLDs with no narrative
                continue

            # Prefer portfolio advisor reasoning, fall back to investment thesis
            detail = reasoning or insight.investment_thesis[:600] or f"{action} signal from agent pipeline."

            rec_rows.append({
                "user_id": state.user_id,
                "ticker": insight.ticker,
                "action": action,
                "detail": detail,
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

        # 3. Insert new recommendations FIRST — errors propagate to mark run failed.
        #    Doing this before deactivating old ones prevents a blank-state window.
        if rec_rows:
            for i in range(0, len(rec_rows), 50):
                self.db.table("recommendations").insert(rec_rows[i:i + 50]).execute()

        # 4. Deactivate previous recommendations (excluding this run's new rows).
        #    Two passes: one for rows with a different run_id, one for legacy NULL rows.
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
            # New recs are already inserted — deactivation is best-effort cleanup.

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
            parts.append(f"sent {insight.sentiment_label}({insight.sentiment_score})")
        if insight.technical_signal:
            parts.append(f"tech {insight.technical_signal}")
        if insight.fundamental_score is not None:
            parts.append(f"fund {insight.fundamental_score:+.2f}")
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
            return "LT eligible — 15-20% cap gains" if insight.lt_eligible else "ST status — 37% ordinary income tax"
        return ""


def _round(v):
    return round(v, 2) if v is not None else None


def _compute_what_changed(prev: dict, curr: "TickerInsight") -> str:
    """Return a newline-separated list of meaningful changes vs the previous insight row.

    Compares action, sentiment label, technical signal, and numeric scores.
    Returns an empty string when nothing significant changed.
    """
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
            bullets.append(f"Conviction: {float(prev_conv):+.2f} → {curr_conv:+.2f} ({sign}{delta:.2f})")

    prev_ss = prev.get("sentiment_score")
    curr_ss = curr.sentiment_score
    if prev_ss is not None and curr_ss is not None:
        delta = curr_ss - float(prev_ss)
        if abs(delta) >= 0.15:
            sign = "+" if delta >= 0 else ""
            bullets.append(f"Sentiment score: {float(prev_ss):+.2f} → {curr_ss:+.2f} ({sign}{delta:.2f})")

    return "\n".join(bullets)
