"""Decision delta analysis for recommendation snapshots vs actual decisions."""

from __future__ import annotations

from typing import Any


DECISION_STATUS_FULLY_EXECUTED = "FULLY_EXECUTED"
DECISION_STATUS_PARTIALLY_EXECUTED = "PARTIALLY_EXECUTED"
DECISION_STATUS_SKIPPED = "SKIPPED"


def _to_float(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def _normalize_action(value: Any) -> str:
    return str(value or "").strip().upper()


def _is_etf_like(ticker: str) -> bool:
    known_etfs = {"VOO", "VTI", "IVV", "SPY", "QQQ", "VYM", "SCHD", "DGRO", "BND", "VXUS", "VEA"}
    return ticker.upper() in known_etfs


def _style_from_text(text: str | None) -> str | None:
    if not text:
        return None
    blob = text.lower()
    income_words = ("income", "dividend", "yield", "cashflow", "defensive")
    growth_words = ("growth", "momentum", "upside", "expansion")
    if any(word in blob for word in income_words):
        return "income"
    if any(word in blob for word in growth_words):
        return "growth"
    return None


def _hhi(weights: list[float]) -> float:
    return sum((w / 100.0) ** 2 for w in weights if w > 0)


def analyzeDecisionDelta(
    recommendation_snapshot: dict[str, Any] | None,
    actual_decisions: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    snapshot = recommendation_snapshot or {}
    actuals = actual_decisions or []
    rec_rows = snapshot.get("normalized_tickers")
    rec_tickers = rec_rows if isinstance(rec_rows, list) else []
    rec_amount_by_ticker: dict[str, float] = {}
    rec_style_by_ticker: dict[str, str | None] = {}

    for row in rec_tickers:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").upper()
        if not ticker:
            continue
        rec_amount_by_ticker[ticker] = _to_float(row.get("amount"))
        rec_style_by_ticker[ticker] = _style_from_text(
            str(row.get("role_label") or row.get("rationale") or "")
        )

    total_recommended = sum(rec_amount_by_ticker.values())
    total_actual = 0.0
    skipped_tickers: list[str] = []
    replaced_tickers: list[dict[str, str | None]] = []
    has_bought = False
    has_skipped = False
    has_replaced = False
    growth_to_income = False
    income_to_growth = False
    single_to_etf = False
    actual_amount_by_bucket: dict[str, float] = {}

    for row in actuals:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").upper()
        replacement_ticker = str(row.get("replacement_ticker") or "").upper()
        action = _normalize_action(row.get("actual_action"))
        actual_amount = _to_float(row.get("actual_amount"))
        replacement_amount = _to_float(row.get("replacement_amount"))
        deployed_amount = max(actual_amount, replacement_amount, 0.0)
        total_actual += deployed_amount

        bucket = replacement_ticker or ticker
        if bucket:
            actual_amount_by_bucket[bucket] = actual_amount_by_bucket.get(bucket, 0.0) + deployed_amount

        if action == "BOUGHT" and deployed_amount > 0:
            has_bought = True
        if action == "SKIPPED" or (deployed_amount <= 0 and not replacement_ticker):
            has_skipped = True
            if ticker:
                skipped_tickers.append(ticker)
        if action == "REPLACED" or replacement_ticker:
            has_replaced = True
            if ticker or replacement_ticker:
                replaced_tickers.append(
                    {
                        "from": ticker or None,
                        "to": replacement_ticker or None,
                        "reason": (str(row.get("reason") or "").strip() or None),
                    }
                )
            if ticker and replacement_ticker and not _is_etf_like(ticker) and _is_etf_like(replacement_ticker):
                single_to_etf = True

            from_style = rec_style_by_ticker.get(ticker) or _style_from_text(str(row.get("reason") or ""))
            to_style = _style_from_text(str(row.get("reason") or ""))
            if from_style == "growth" and to_style == "income":
                growth_to_income = True
            if from_style == "income" and to_style == "growth":
                income_to_growth = True

    rec_weights: list[float] = []
    if total_recommended > 0:
        rec_weights = [(amount / total_recommended) * 100.0 for amount in rec_amount_by_ticker.values()]
    actual_weights: list[float] = []
    if total_actual > 0:
        actual_weights = [(amount / total_actual) * 100.0 for amount in actual_amount_by_bucket.values()]
    concentration_change = _hhi(actual_weights) - _hhi(rec_weights)

    all_skipped = len(actuals) > 0 and all(
        _normalize_action(row.get("actual_action")) == "SKIPPED"
        or max(_to_float(row.get("actual_amount")), _to_float(row.get("replacement_amount"))) <= 0
        for row in actuals
        if isinstance(row, dict)
    )
    actual_by_ticker: dict[str, dict[str, Any]] = {}
    for row in actuals:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").upper()
        if ticker:
            actual_by_ticker[ticker] = row

    fully_executed = len(rec_amount_by_ticker) > 0 and all(
        ticker in actual_by_ticker
        and _normalize_action(actual_by_ticker[ticker].get("actual_action")) == "BOUGHT"
        and max(
            _to_float(actual_by_ticker[ticker].get("actual_amount")),
            _to_float(actual_by_ticker[ticker].get("replacement_amount")),
        )
        >= rec_amount
        for ticker, rec_amount in rec_amount_by_ticker.items()
    )
    if fully_executed:
        status = DECISION_STATUS_FULLY_EXECUTED
    elif all_skipped or (total_actual <= 0 and not has_bought and not has_replaced):
        status = DECISION_STATUS_SKIPPED
    else:
        status = DECISION_STATUS_PARTIALLY_EXECUTED

    deploy_delta = total_actual - total_recommended
    execution_gap_percent = ((total_recommended - total_actual) / total_recommended * 100.0) if total_recommended > 0 else 0.0
    style_shift = "growth_to_income" if growth_to_income else ("income_to_growth" if income_to_growth else None)

    if style_shift == "growth_to_income" or single_to_etf or deploy_delta < 0:
        risk_behavior = "more_conservative"
    elif style_shift == "income_to_growth" or deploy_delta > 0:
        risk_behavior = "more_aggressive"
    else:
        risk_behavior = "aligned"

    return {
        "status": status,
        "decision_delta": {
            "total_recommended": round(total_recommended, 2),
            "total_actual": round(total_actual, 2),
            "deploy_delta": round(deploy_delta, 2),
            "skipped_tickers": sorted(set(skipped_tickers)),
            "replaced_tickers": replaced_tickers,
            "category_shift": {
                "growth_to_income": growth_to_income,
                "single_to_etf": single_to_etf,
                "concentration_change": round(concentration_change, 6),
            },
        },
        "risk_behavior": risk_behavior,
        "style_shift": style_shift,
        "execution_gap_percent": round(execution_gap_percent, 2),
        "signals": {
            "has_bought": has_bought,
            "has_skipped": has_skipped,
            "has_replaced": has_replaced,
        },
    }
