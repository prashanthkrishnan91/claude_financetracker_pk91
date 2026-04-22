"""AI pipeline context aggregation layer.

Separates data gathering from LLM orchestration so the pipeline always
follows: DB → Context Builder → Single LLM Call → Persist Results.
"""

from .context_builder import build_portfolio_context

__all__ = ["build_portfolio_context"]
