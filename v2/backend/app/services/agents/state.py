"""Shared state objects for the multi-agent pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class TickerInsight:
    """Per-ticker output from the three analyst agents + PM synthesis.

    Mirrors the columns on the `agent_insights` table.
    """

    ticker: str
    name: str = ""
    category: str = ""

    # Position snapshot (pre-computed so agents don't re-query)
    shares: float = 0.0
    avg_cost: float = 0.0
    current_price: Optional[float] = None
    current_weight_pct: float = 0.0      # weight of this position in the portfolio
    pnl_pct: Optional[float] = None
    lt_eligible: bool = False
    target_price: Optional[float] = None

    # Sentiment agent
    sentiment_score: Optional[float] = None      # -1.0 .. +1.0
    sentiment_label: Optional[str] = None         # bullish/neutral/bearish
    sentiment_summary: str = ""
    headlines_used: list[str] = field(default_factory=list)

    # Technical agent
    technical_signal: Optional[str] = None        # BUY/HOLD/SELL/NEUTRAL
    technical_summary: str = ""
    tech_metrics: dict[str, Any] = field(default_factory=dict)

    # Fundamental agent
    fundamental_score: Optional[float] = None     # -1.0 .. +1.0
    fundamental_summary: str = ""
    fundamentals: dict[str, Any] = field(default_factory=dict)

    # Portfolio manager synthesis
    conviction_score: Optional[float] = None      # -1.0 .. +1.0 (weighted blend)
    investment_thesis: str = ""
    suggested_action: str = "HOLD"                 # BUY/SELL/TRIM/HOLD/REVIEW
    suggested_allocation: float = 0.0              # dollars

    def to_insight_row(self, run_id: str, user_id: str) -> dict[str, Any]:
        """Map to the agent_insights table schema."""
        return {
            "run_id": run_id,
            "user_id": user_id,
            "ticker": self.ticker,
            "investment_thesis": self.investment_thesis,
            "sentiment_score": _round(self.sentiment_score),
            "sentiment_label": self.sentiment_label,
            "technical_signal": self.technical_signal,
            "technical_summary": self.technical_summary,
            "fundamental_score": _round(self.fundamental_score),
            "fundamental_summary": self.fundamental_summary,
            "conviction_score": _round(self.conviction_score),
            "suggested_allocation": round(self.suggested_allocation, 2),
            "suggested_action": self.suggested_action,
        }


def _round(v: Optional[float]) -> Optional[float]:
    return round(v, 2) if v is not None else None


@dataclass
class AgentState:
    """Graph-wide state passed between nodes."""

    user_id: str
    run_id: str
    tickers: list[str]
    deposit_amount: float = 0.0
    sale_proceeds: float = 0.0
    insights: dict[str, TickerInsight] = field(default_factory=dict)
    total_portfolio_value: float = 0.0
    category_weights: dict[str, float] = field(default_factory=dict)
    pm_summary: str = ""
    portfolio_advice: dict[str, Any] = field(default_factory=dict)

    @property
    def cash_to_deploy(self) -> float:
        return self.deposit_amount + self.sale_proceeds
