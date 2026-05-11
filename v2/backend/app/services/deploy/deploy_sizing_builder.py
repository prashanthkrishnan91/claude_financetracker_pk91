"""Deploy Stage 2.1 — sizing context builder.

Pure function that accepts explicit dict inputs and returns a typed
DeploySizingInputBundle. No IO, no DB, no LLM calls.

This is a mapping / normalization layer. It accepts a raw portfolio
snapshot dict and converts it into the Deploy sizing contract types.
It does not evaluate dollar amounts, does not call any provider,
and does not modify Intel decisions.

Callers that have real brokerage data can pass it here; callers that
do not should pass None for missing fields — the trust model will mark
those inputs as MISSING and suppression will follow automatically.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .deploy_sizing_contracts import (
    DeployCashInput,
    DeployPortfolioSizingInput,
    DeployPositionSizingInput,
    DeploySizingInputBundle,
    DeploySizingPolicyPlaceholder,
    DeploySizingTrustStatus,
    DeployTargetAllocationInput,
)


def build_sizing_context_from_portfolio_snapshot(
    portfolio_snapshot: Dict[str, Any],
) -> DeploySizingInputBundle:
    """Build a DeploySizingInputBundle from a raw portfolio snapshot dict.

    Accepts a dict with the following optional keys:
      - available_cash_usd (float | None): Available cash for deployment.
      - cash_trust_status (str): One of the DeploySizingTrustStatus values.
          Defaults to "MISSING" if absent.
      - cash_source_label (str): Source identifier for cash data.
      - total_portfolio_value_usd (float | None): Total portfolio market value.
      - portfolio_trust_status (str): Trust status for portfolio total.
          Defaults to "MISSING" if absent.
      - portfolio_source_label (str): Source identifier for portfolio data.
      - positions (list[dict]): Per-ticker position data, each with:
            - ticker (str)
            - current_market_value_usd (float | None)
            - current_weight (float | None)
            - trust_status (str): Trust status. Defaults to "MISSING".
            - source_label (str)
      - target_allocations (list[dict]): Per-ticker target allocations, each with:
            - ticker (str)
            - target_weight (float | None): None if not defined.
            - trust_status (str): Defaults to "NOT_EVALUATED".
      - minimum_trade_usd (float | None): Minimum trade threshold. Placeholder.
      - rounding_policy (str): Rounding policy name. Placeholder.

    All sizing data in Stage 2.1 is treated as NOT_CERTIFIED unless the caller
    explicitly sets trust_status = "CERTIFIED". Missing fields produce MISSING
    trust status and suppress exact-dollar readiness.

    This function performs NO IO and does NOT evaluate any dollar amounts.
    """
    if portfolio_snapshot is None:
        portfolio_snapshot = {}

    # --- Cash ---
    cash_usd = portfolio_snapshot.get("available_cash_usd")
    raw_cash_trust = portfolio_snapshot.get("cash_trust_status", "MISSING")
    cash_trust = _parse_trust_status(raw_cash_trust, fallback=DeploySizingTrustStatus.MISSING)
    cash_source = portfolio_snapshot.get("cash_source_label", "not_provided")
    cash_input = DeployCashInput(
        available_cash_usd=cash_usd,
        trust_status=cash_trust,
        source_label=cash_source,
    )

    # --- Portfolio total ---
    portfolio_value = portfolio_snapshot.get("total_portfolio_value_usd")
    raw_port_trust = portfolio_snapshot.get("portfolio_trust_status", "MISSING")
    port_trust = _parse_trust_status(raw_port_trust, fallback=DeploySizingTrustStatus.MISSING)
    port_source = portfolio_snapshot.get("portfolio_source_label", "not_provided")
    portfolio_input = DeployPortfolioSizingInput(
        total_portfolio_value_usd=portfolio_value,
        trust_status=port_trust,
        source_label=port_source,
    )

    # --- Per-ticker positions ---
    positions: Dict[str, DeployPositionSizingInput] = {}
    for pos_dict in (portfolio_snapshot.get("positions") or []):
        ticker = str(pos_dict.get("ticker") or "UNKNOWN")
        mkt_val = pos_dict.get("current_market_value_usd")
        weight = pos_dict.get("current_weight")
        raw_pos_trust = pos_dict.get("trust_status", "MISSING")
        pos_trust = _parse_trust_status(raw_pos_trust, fallback=DeploySizingTrustStatus.MISSING)
        pos_source = pos_dict.get("source_label", "not_provided")
        positions[ticker] = DeployPositionSizingInput(
            ticker=ticker,
            current_market_value_usd=mkt_val,
            current_weight=weight,
            trust_status=pos_trust,
            source_label=pos_source,
        )

    # --- Target allocations (placeholders) ---
    target_allocations: Dict[str, DeployTargetAllocationInput] = {}
    for ta_dict in (portfolio_snapshot.get("target_allocations") or []):
        ticker = str(ta_dict.get("ticker") or "UNKNOWN")
        tgt_weight = ta_dict.get("target_weight")
        raw_ta_trust = ta_dict.get("trust_status", "NOT_EVALUATED")
        ta_trust = _parse_trust_status(raw_ta_trust, fallback=DeploySizingTrustStatus.NOT_EVALUATED)
        target_allocations[ticker] = DeployTargetAllocationInput(
            ticker=ticker,
            target_weight=tgt_weight,
            trust_status=ta_trust,
            source_label=ta_dict.get("source_label", "not_evaluated_yet"),
        )

    # --- Policy placeholders ---
    min_trade = portfolio_snapshot.get("minimum_trade_usd")
    rounding = portfolio_snapshot.get("rounding_policy", "not_implemented_yet")
    policy = DeploySizingPolicyPlaceholder(
        minimum_trade_usd=min_trade,
        rounding_policy=rounding,
        trust_status=DeploySizingTrustStatus.UNSUPPORTED,
    )

    return DeploySizingInputBundle(
        cash=cash_input,
        portfolio=portfolio_input,
        positions=positions,
        target_allocations=target_allocations,
        policy=policy,
        schema_version="deploy_sizing_v1_contract",
    )


def _parse_trust_status(
    raw: Any,
    fallback: DeploySizingTrustStatus = DeploySizingTrustStatus.NOT_EVALUATED,
) -> DeploySizingTrustStatus:
    """Parse a trust status string into a DeploySizingTrustStatus enum value.

    Returns fallback if raw is unrecognized or None.
    """
    if raw is None:
        return fallback
    try:
        return DeploySizingTrustStatus(str(raw).upper())
    except ValueError:
        return fallback
