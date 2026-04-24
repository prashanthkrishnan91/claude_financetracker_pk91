"""Recommendation models — Buy/Sell/Trim/Hold engine output."""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class RecommendationBase(BaseModel):
    """Shared recommendation fields."""
    ticker: str
    action: str = Field(pattern="^(BUY|SELL|TRIM|HOLD|REVIEW)$")
    detail: str
    rationale: Optional[str] = None
    urgency: int = Field(default=0, ge=0, le=4)
    tax_note: Optional[str] = None
    drip_note: Optional[str] = None


class RecommendationResponse(RecommendationBase):
    """Recommendation returned by API."""
    id: UUID
    user_id: UUID
    is_active: bool
    created_at: datetime
    resolved_at: Optional[datetime] = None
    resolution: Optional[str] = None

    model_config = {"from_attributes": True}


class RecommendationResolve(BaseModel):
    """Resolve (accept/reject/defer) a recommendation."""
    resolution: str = Field(pattern="^(accepted|rejected|deferred|expired)$")
    notes: Optional[str] = None


class InsightCard(BaseModel):
    """Frontend-ready insight card with all display data."""
    id: UUID
    ticker: str
    name: str
    action: str
    detail: str
    rationale: str
    urgency: int
    color: str          # CSS color key: green/red/gold/blue/purple/orange/gray
    tax_note: str
    drip_note: str
    current_price: Optional[float] = None
    pnl_pct: Optional[float] = None
    category: str       # Core, ETF, Crypto, etc.
    sector: Optional[str] = None

    # Multi-agent fields (populated from the agent pipeline)
    investment_thesis: Optional[str] = None
    sentiment_score: Optional[float] = None
    sentiment_label: Optional[str] = None
    technical_signal: Optional[str] = None
    conviction_score: Optional[float] = None
    suggested_allocation: Optional[float] = None
    agent_run_id: Optional[UUID] = None
    what_changed: Optional[str] = None

    # Data-quality UX fields — drive the frontend confidence banner.
    # data_confidence_score: 0–1 (derived from conviction / signal completeness)
    # data_quality_label:    HIGH / MEDIUM / LOW
    # reason_tags:           e.g. ["fallback_used", "low_data", "api_failure"]
    data_confidence_score: Optional[float] = None
    data_quality_label: Optional[str] = None
    reason_tags: Optional[list[str]] = None

    # Phase 3 — per-ticker analyst projection onto the card. The analyst
    # verdict lives on ``agent_insights`` but we surface its drivers +
    # risks directly on the InsightCard so the frontend doesn't have to
    # issue a second request per ticker. Null on pre-Phase-3 runs.
    analyst_action: Optional[str] = None
    analyst_conviction: Optional[float] = None
    analyst_confidence: Optional[float] = None
    analyst_drivers: Optional[list[str]] = None
    analyst_risks: Optional[list[str]] = None
    analyst_used_fallback: Optional[bool] = None
    # Canonical reasoning contract projected for UI stability.
    summary: Optional[str] = None
    reasoning_summary: Optional[str] = None
    thesis: Optional[str] = None
    why_this_matters: Optional[str] = None
    key_drivers: Optional[list[str]] = None
    main_risks: Optional[list[str]] = None
    confidence: Optional[float] = None
    conviction: Optional[float] = None
    supporting_evidence: Optional[list[str]] = None
    plain_language_explanation: Optional[str] = None
    fallback_flags: Optional[list[str]] = None
    analysis_source: Optional[str] = None  # live_llm | cached_run | deterministic_fallback


class AgentRunStatus(BaseModel):
    """Status snapshot of an in-flight or completed agent run.

    Phase 4-6 extensions:
      * ``portfolio_synthesis`` — full PortfolioSynthesis dict
        (portfolio_bias, key_themes, risk_concentrations,
        overexposure_flags, rebalancing_suggestions, summary,
        used_fallback). Drives the portfolio-insights panel.
      * ``run_mode`` / ``run_mode_decision`` — Phase 5 mode badge +
        human-facing explanation for the DEGRADED-mode banner.
      * ``cost_metrics`` — per-run LLM call counts + estimated cost.
    """
    id: UUID
    status: str                 # queued | running | completed | failed
    current_agent: Optional[str] = None
    progress_pct: int = 0
    tickers: list[str] = []
    deposit_amount: float = 0
    sale_proceeds: float = 0
    allocation: dict = {}
    summary: Optional[str] = None
    error_message: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    # Phase 4 — dedicated portfolio synthesis output.
    portfolio_synthesis: Optional[dict] = None
    synthesis_used_fallback: Optional[bool] = None
    # Phase 5 — run mode + cost.
    run_mode: Optional[str] = None          # FULL | DEGRADED
    run_mode_decision: Optional[dict] = None
    cost_metrics: Optional[dict] = None


class AgentRunCreate(BaseModel):
    """Payload for POST /recommendations/refresh — kicks off a pipeline run."""
    deposit_amount: Optional[float] = None   # defaults to user.deposit_amount
    sale_proceeds: Optional[float] = 0.0


class AgentRunQueued(BaseModel):
    """Immediate response from POST /recommendations/refresh."""
    job_id: UUID
    status: str
    message: str


class AgentInsight(BaseModel):
    """Full per-ticker agent output for the drill-down view.

    Phase 3 additions:
      * ``analyst_verdict`` — raw analyst output (action ∈ {BUY, HOLD,
        REDUCE, INSUFFICIENT_DATA}, conviction, key_drivers, risks,
        confidence, used_fallback). Used by the frontend card so every
        ticker shows WHY the signal exists, not just the mapped action.
      * ``analyst_confidence`` — quick access to the analyst's
        self-reported confidence without drilling into the JSONB.
    """
    id: UUID
    run_id: Optional[UUID] = None
    ticker: str
    investment_thesis: Optional[str] = None
    sentiment_score: Optional[float] = None
    sentiment_label: Optional[str] = None
    technical_signal: Optional[str] = None
    technical_summary: Optional[str] = None
    fundamental_score: Optional[float] = None
    fundamental_summary: Optional[str] = None
    conviction_score: Optional[float] = None
    suggested_allocation: Optional[float] = None
    suggested_action: Optional[str] = None
    created_at: Optional[str] = None
    what_changed: Optional[str] = None
    # Phase 3 per-ticker analyst verdict (raw).
    analyst_verdict: Optional[dict] = None
    analyst_confidence: Optional[float] = None


class DecisionLogEntry(BaseModel):
    """Decision log entry returned by API."""
    id: UUID
    recommendation_id: Optional[UUID] = None
    ticker: str
    decision: str
    notes: Optional[str] = None
    price_at_decision: Optional[float] = None
    shares_at_decision: Optional[float] = None
    current_price: Optional[float] = None
    return_pct: Optional[float] = None
    status: str = "active"
    closed_at: Optional[datetime] = None
    created_at: datetime
    strategy_tag: Optional[str] = None
    confidence_score: Optional[float] = None

    model_config = {"from_attributes": True}


class DecisionLogCreate(BaseModel):
    """Create a decision log entry."""
    recommendation_id: Optional[UUID] = None
    ticker: str
    decision: str = Field(pattern="^(accepted|rejected|modified|deferred)$")
    notes: Optional[str] = None
    price_at_decision: Optional[float] = None
    shares_at_decision: Optional[float] = None
    strategy_tag: Optional[str] = None
    confidence_score: Optional[float] = None


class StrategyPerformance(BaseModel):
    """Aggregated performance stats grouped by strategy_tag."""
    strategy_tag: str
    avg_return: Optional[float] = None
    win_rate: Optional[float] = None
    total_trades: int
