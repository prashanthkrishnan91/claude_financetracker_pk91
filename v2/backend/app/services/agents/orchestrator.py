"""Hand-rolled async orchestrator for the multi-agent trading pipeline.

No LangGraph. Just asyncio.gather with status hooks that write progress to
the `agent_runs` table so the UI can poll for live updates.

Pipeline phases:
  1. bootstrap       — load positions, prices, build AgentState
  2. sentiment       — fan out sentiment agent per ticker
  3. technical       — fan out technical agent per ticker
  4. fundamental     — fan out fundamental agent per ticker
  5. portfolio_mgr   — single LLM call, deposit allocation
  6. persist         — write agent_insights + recommendations rows

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
        """Execute the full pipeline for a given run_id."""
        try:
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
                return AgentPipelineResult(run_id=run_id, status="completed",
                                           summary="No positions.", insights=[])

            async with await _build_http_client() as http_client:
                # Phase 2: Sentiment
                await self._update_run(run_id, current_agent="Analyzing Sentiment", progress=20)
                await self._fanout(
                    state,
                    lambda i: run_sentiment_agent(i, self._llm, http_client, self._finnhub_key),
                )

                # Phase 3: Technical
                await self._update_run(run_id, current_agent="Evaluating Technicals", progress=45)
                await self._fanout(
                    state,
                    lambda i: run_technical_agent(i, self._llm, http_client, self._polygon_key),
                )

                # Phase 4: Fundamentals
                await self._update_run(run_id, current_agent="Reviewing Fundamentals", progress=70)
                await self._fanout(
                    state,
                    lambda i: run_fundamental_agent(i, self._llm, http_client),
                )

                # Phase 5: Portfolio Manager synthesis
                await self._update_run(run_id, current_agent="Portfolio Manager Deliberating", progress=85)
                await run_portfolio_manager(state, self._llm)

            # Phase 6: Persist results
            await self._update_run(run_id, current_agent="Saving Insights", progress=95)
            self._persist(state)

            # Build allocation map for the run row
            allocation_map = {
                i.ticker: i.suggested_allocation
                for i in state.insights.values()
                if i.suggested_allocation > 0
            }
            await self._update_run(
                run_id,
                status="completed",
                current_agent="Completed",
                progress=100,
                summary=state.pm_summary,
                allocation=allocation_map,
            )

            return AgentPipelineResult(
                run_id=run_id,
                status="completed",
                summary=state.pm_summary,
                insights=list(state.insights.values()),
            )

        except Exception as exc:
            logger.exception("Agent pipeline failed for run %s", run_id)
            await self._update_run(
                run_id,
                status="failed",
                current_agent="Failed",
                progress=100,
                error_message=str(exc)[:500],
            )
            return AgentPipelineResult(
                run_id=run_id,
                status="failed",
                summary=f"Pipeline error: {exc}",
                insights=[],
            )

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
        """Run `coro_factory(insight)` for each ticker with bounded concurrency."""
        sem = asyncio.Semaphore(_AGENT_CONCURRENCY)

        async def _wrap(insight: TickerInsight):
            async with sem:
                try:
                    await coro_factory(insight)
                except Exception as exc:
                    logger.warning("Agent failed for %s: %s", insight.ticker, exc)

        await asyncio.gather(*[_wrap(i) for i in state.insights.values()])

    def _persist(self, state: AgentState) -> None:
        """Write agent_insights + refresh the recommendations table."""
        now = datetime.now(timezone.utc).isoformat()

        # 0. Fetch previous insights per ticker for diff computation (before inserting new rows)
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

        # 1. agent_insights rows
        insight_rows = []
        for insight in state.insights.values():
            row = insight.to_insight_row(run_id=state.run_id, user_id=state.user_id)
            wc = what_changed_map.get(insight.ticker)
            if wc:
                row["what_changed"] = wc
            insight_rows.append(row)

        if insight_rows:
            try:
                self.db.table("agent_insights").insert(insight_rows).execute()
            except Exception as exc:
                logger.warning("agent_insights insert failed: %s", exc)

        # 2. Deactivate previous active recommendations
        try:
            self.db.table("recommendations").update({
                "is_active": False,
                "resolution": "expired",
                "resolved_at": now,
            }).eq("user_id", state.user_id).eq("is_active", True).execute()
        except Exception as exc:
            logger.warning("Failed to expire old recommendations: %s", exc)

        # 3. Insert fresh recommendations linked to this run
        rec_rows = []
        for insight in state.insights.values():
            if not insight.investment_thesis and insight.suggested_action == "HOLD":
                # Keep the table slim — skip pure HOLDs with no narrative
                continue
            rec_rows.append({
                "user_id": state.user_id,
                "ticker": insight.ticker,
                "action": insight.suggested_action,
                "detail": insight.investment_thesis[:600] or f"{insight.suggested_action} signal from agent pipeline.",
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
            try:
                for i in range(0, len(rec_rows), 50):
                    self.db.table("recommendations").insert(rec_rows[i:i + 50]).execute()
            except Exception as exc:
                logger.warning("recommendations insert failed: %s", exc)

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
