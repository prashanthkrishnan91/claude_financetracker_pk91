"""Multi-agent reasoning engine — TradingAgents-inspired.

Hand-rolled async orchestrator. No LangGraph dependency.

Graph shape (linear with final join):

    ┌── sentiment_agent ──┐
    │                     │
    ├── technical_agent ──┼──→ portfolio_manager ──→ allocations
    │                     │
    └── fundamental_agent ┘

Each analyst node runs in parallel per ticker; the portfolio manager
synthesises a conviction score, thesis, and dollar allocation against
the user's deposit + sale proceeds and current concentration.
"""

from .orchestrator import AgentOrchestrator, AgentPipelineResult
from .state import AgentState, TickerInsight

__all__ = [
    "AgentOrchestrator",
    "AgentPipelineResult",
    "AgentState",
    "TickerInsight",
]
