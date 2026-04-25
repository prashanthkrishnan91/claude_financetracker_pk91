"""Decision log service — persists and retrieves deploy-plan decisions."""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from statistics import median
from typing import Any

from ..config import get_settings
from ..database import get_supabase_client
from .decision_delta import analyzeDecisionDelta

logger = logging.getLogger(__name__)


INCOME_ETF_HINTS = {"SCHD", "VYM", "DGRO", "HDV", "JEPI", "DIVO", "BND", "SCHY"}
ETF_HINTS = {
    "VOO", "VTI", "IVV", "SPY", "QQQ", "VYM", "SCHD", "DGRO", "BND", "VXUS", "VEA",
    "SCHY", "HDV", "JEPI", "DIVO",
}


def _to_float(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def _is_etf_like(ticker: str) -> bool:
    return ticker.upper() in ETF_HINTS


def _weighted_median(values: list[float], weights: list[float]) -> float:
    """Return weighted median for aligned ``values``/``weights`` arrays."""
    if not values or len(values) != len(weights):
        return 1.0
    pairs = sorted(
        [(float(v), max(0.0, float(w))) for v, w in zip(values, weights)],
        key=lambda item: item[0],
    )
    total_weight = sum(w for _, w in pairs)
    if total_weight <= 0:
        return median(values)
    threshold = total_weight / 2.0
    running = 0.0
    for value, weight in pairs:
        running += weight
        if running >= threshold:
            return value
    return pairs[-1][0]


def _pct_return(entry_price: float, current_price: float) -> float:
    if entry_price <= 0:
        return 0.0
    return ((current_price - entry_price) / entry_price) * 100.0


def _to_iso_utc(ts: float | None = None) -> str:
    if ts is None:
        return datetime.now(timezone.utc).isoformat()
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(coro)).result()


class DecisionLogService:
    def __init__(self) -> None:
        self.client = get_supabase_client()
        self._price_service = None

    def _make_price_service(self):
        from .price_engine import PriceService

        settings = get_settings()
        return PriceService(
            finnhub_key=settings.finnhub_api_key or "",
            alpaca_key=settings.alpaca_api_key or "",
            alpaca_secret=settings.alpaca_secret_key or "",
            polygon_key=settings.polygon_api_key or "",
        )

    def _ensure_price_service(self):
        if self._price_service is None:
            self._price_service = self._make_price_service()
        return self._price_service

    async def _fetch_price_map_async(self, tickers: list[str]) -> dict[str, dict[str, Any]]:
        unique = sorted({str(t or "").upper().strip() for t in tickers if str(t or "").strip()})
        if not unique:
            return {}
        service = self._ensure_price_service()
        try:
            results = await service.fetch_prices(unique)
        except Exception:
            logger.exception("decision_log.prices.fetch_failed tickers=%s", unique)
            return {}
        snapshot: dict[str, dict[str, Any]] = {}
        for ticker in unique:
            result = results.get(ticker)
            if not result or not getattr(result, "is_valid", False):
                continue
            snapshot[ticker] = {
                "price": float(result.mid_price),
                "timestamp": _to_iso_utc(getattr(result, "timestamp", None)),
            }
        return snapshot

    def _extract_recommendation_tickers(self, recommendation_snapshot: dict[str, Any] | None) -> list[str]:
        snapshot = recommendation_snapshot if isinstance(recommendation_snapshot, dict) else {}
        rows = snapshot.get("normalized_tickers")
        if not isinstance(rows, list):
            return []
        tickers: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            ticker = str(row.get("ticker") or "").strip().upper()
            if ticker:
                tickers.append(ticker)
        return tickers

    def _extract_replacement_tickers(self, actual_decisions: list[dict[str, Any]] | None) -> list[str]:
        decisions = actual_decisions if isinstance(actual_decisions, list) else []
        tickers: list[str] = []
        for item in decisions:
            if not isinstance(item, dict):
                continue
            replacement = str(item.get("replacement_ticker") or "").strip().upper()
            if replacement:
                tickers.append(replacement)
        return tickers

    def create(self, user_id: str, data: dict[str, Any]) -> dict[str, Any]:
        recommendation_snapshot = data.get("recommendation_snapshot")
        actual_decisions = data.get("actual_decisions")
        analysis = analyzeDecisionDelta(
            recommendation_snapshot=recommendation_snapshot,
            actual_decisions=actual_decisions,
        )
        tickers_for_snapshot = self._extract_recommendation_tickers(recommendation_snapshot) + self._extract_replacement_tickers(actual_decisions)
        price_snapshot = _run_async(self._fetch_price_map_async(tickers_for_snapshot))
        payload = {
            **data,
            "user_id": user_id,
            "price_snapshot": price_snapshot,
            "status": analysis["status"],
            "decision_delta": analysis["decision_delta"],
            "risk_behavior": analysis["risk_behavior"],
            "style_shift": analysis["style_shift"],
            "execution_gap_percent": analysis["execution_gap_percent"],
        }
        logger.info("decision_log.create user_id=%s status=%s", user_id, payload.get("status"))
        result = self.client.table("decision_logs").insert(payload).execute()
        return result.data[0] if result.data else {}

    def list(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        logger.info("decision_log.list user_id=%s limit=%s", user_id, limit)
        result = (
            self.client.table("decision_logs")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []

    def get(self, user_id: str, decision_log_id: str, *, evaluate_if_missing: bool = False) -> dict[str, Any] | None:
        logger.info("decision_log.get user_id=%s id=%s", user_id, decision_log_id)
        result = (
            self.client.table("decision_logs")
            .select("*")
            .eq("id", decision_log_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        if not rows:
            return None
        row = rows[0]
        if evaluate_if_missing and not isinstance(row.get("performance_snapshot"), dict):
            evaluated = self.evaluateDecisionPerformance(user_id=user_id, decision_log_id=decision_log_id)
            return evaluated or row
        return row

    def update(self, user_id: str, decision_log_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        logger.info("decision_log.update user_id=%s id=%s", user_id, decision_log_id)
        payload = dict(patch)
        should_recompute = "actual_decisions" in payload or "recommendation_snapshot" in payload
        if should_recompute:
            current = self.get(user_id=user_id, decision_log_id=decision_log_id)
            if not current:
                return None
            recommendation_snapshot = payload.get("recommendation_snapshot", current.get("recommendation_snapshot"))
            actual_decisions = payload.get("actual_decisions", current.get("actual_decisions"))
            analysis = analyzeDecisionDelta(
                recommendation_snapshot=recommendation_snapshot,
                actual_decisions=actual_decisions,
            )
            payload.update(
                {
                    "status": analysis["status"],
                    "decision_delta": analysis["decision_delta"],
                    "risk_behavior": analysis["risk_behavior"],
                    "style_shift": analysis["style_shift"],
                    "execution_gap_percent": analysis["execution_gap_percent"],
                }
            )
        if not should_recompute:
            payload.pop("status", None)
        result = (
            self.client.table("decision_logs")
            .update(payload)
            .eq("id", decision_log_id)
            .eq("user_id", user_id)
            .execute()
        )
        rows = result.data or []
        return rows[0] if rows else None

    def evaluateDecisionPerformance(self, user_id: str, decision_log_id: str) -> dict[str, Any] | None:
        row = self.get(user_id=user_id, decision_log_id=decision_log_id, evaluate_if_missing=False)
        if not row:
            return None

        recommendation_snapshot = row.get("recommendation_snapshot")
        snapshot_rows = (
            recommendation_snapshot.get("normalized_tickers")
            if isinstance(recommendation_snapshot, dict)
            else []
        )
        rec_rows = snapshot_rows if isinstance(snapshot_rows, list) else []
        actual_list = row.get("actual_decisions") if isinstance(row.get("actual_decisions"), list) else []
        actual_by_ticker: dict[str, dict[str, Any]] = {}
        for item in actual_list:
            if not isinstance(item, dict):
                continue
            base_ticker = str(item.get("ticker") or "").strip().upper()
            if base_ticker:
                actual_by_ticker[base_ticker] = item

        existing_snapshot = row.get("price_snapshot") if isinstance(row.get("price_snapshot"), dict) else {}
        tickers_needed = self._extract_recommendation_tickers(recommendation_snapshot) + self._extract_replacement_tickers(actual_list)
        missing_tickers = [t for t in sorted(set(tickers_needed)) if t and t not in existing_snapshot]
        fetched_snapshot = _run_async(self._fetch_price_map_async(missing_tickers)) if missing_tickers else {}
        merged_price_snapshot = {**existing_snapshot, **fetched_snapshot}

        current_prices = _run_async(self._fetch_price_map_async(tickers_needed))

        per_ticker: list[dict[str, Any]] = []
        rec_weighted_sum = 0.0
        rec_weight_total = 0.0
        actual_weighted_sum = 0.0
        actual_weight_total = 0.0

        for rec in rec_rows:
            if not isinstance(rec, dict):
                continue
            ticker = str(rec.get("ticker") or "").strip().upper()
            if not ticker:
                continue
            recommended_amount = _to_float(rec.get("amount"))
            rec_entry = merged_price_snapshot.get(ticker) if isinstance(merged_price_snapshot.get(ticker), dict) else {}
            rec_now = current_prices.get(ticker) if isinstance(current_prices.get(ticker), dict) else {}
            rec_entry_price = _to_float(rec_entry.get("price"))
            rec_current_price = _to_float(rec_now.get("price"))
            recommended_return_pct = _pct_return(rec_entry_price, rec_current_price)

            actual_decision = actual_by_ticker.get(ticker) or {}
            action = str(actual_decision.get("actual_action") or "BOUGHT").strip().upper()
            actual_ticker = ticker
            if action == "REPLACED":
                replacement = str(actual_decision.get("replacement_ticker") or "").strip().upper()
                if replacement:
                    actual_ticker = replacement
            actual_amount = _to_float(actual_decision.get("actual_amount"))
            exposure_amount = 0.0 if action == "SKIPPED" else (actual_amount if actual_amount > 0 else recommended_amount)
            actual_entry = merged_price_snapshot.get(actual_ticker) if isinstance(merged_price_snapshot.get(actual_ticker), dict) else {}
            actual_now = current_prices.get(actual_ticker) if isinstance(current_prices.get(actual_ticker), dict) else {}
            actual_entry_price = _to_float(actual_entry.get("price"))
            actual_current_price = _to_float(actual_now.get("price"))
            actual_return_pct = 0.0 if exposure_amount <= 0 else _pct_return(actual_entry_price, actual_current_price)
            delta_pct = actual_return_pct - recommended_return_pct

            per_ticker.append(
                {
                    "ticker": ticker,
                    "recommended_return_pct": round(recommended_return_pct, 4),
                    "actual_return_pct": round(actual_return_pct, 4),
                    "delta_pct": round(delta_pct, 4),
                }
            )

            if recommended_amount > 0:
                rec_weighted_sum += recommended_return_pct * recommended_amount
                rec_weight_total += recommended_amount
            if exposure_amount > 0:
                actual_weighted_sum += actual_return_pct * exposure_amount
                actual_weight_total += exposure_amount

        total_recommended = rec_weighted_sum / rec_weight_total if rec_weight_total > 0 else 0.0
        total_actual = actual_weighted_sum / actual_weight_total if actual_weight_total > 0 else 0.0
        total_delta = total_actual - total_recommended

        best = max(per_ticker, key=lambda item: item["delta_pct"], default=None)
        worst = min(per_ticker, key=lambda item: item["delta_pct"], default=None)
        performance_snapshot = {
            "evaluated_at": _to_iso_utc(),
            "portfolio": {
                "recommended_return": round(total_recommended, 4),
                "actual_return": round(total_actual, 4),
                "delta": round(total_delta, 4),
                "total_recommended_return": round(total_recommended, 4),
                "total_actual_return": round(total_actual, 4),
                "total_delta": round(total_delta, 4),
                "best_decision": best,
                "worst_decision": worst,
            },
            "per_ticker": per_ticker,
        }

        updated = (
            self.client.table("decision_logs")
            .update(
                {
                    "price_snapshot": merged_price_snapshot,
                    "performance_snapshot": performance_snapshot,
                }
            )
            .eq("id", decision_log_id)
            .eq("user_id", user_id)
            .execute()
        )
        rows = updated.data or []
        return rows[0] if rows else row

    def delete(self, user_id: str, decision_log_id: str) -> bool:
        logger.info("decision_log.delete user_id=%s id=%s", user_id, decision_log_id)
        result = (
            self.client.table("decision_logs")
            .delete()
            .eq("id", decision_log_id)
            .eq("user_id", user_id)
            .execute()
        )
        return bool(result.data)

    def getUserBehaviorProfile(self, user_id: str, limit: int = 10) -> dict[str, Any]:
        """Aggregate recent decision log behavior for soft adaptive deploy nudges."""
        rows = self.list(user_id=user_id, limit=limit)
        if not rows:
            return {
                "avg_deploy_ratio": 1.0,
                "stable_deploy_ratio": 1.0,
                "skip_rate": 0.0,
                "replace_rate": 0.0,
                "prefers_etf": False,
                "prefers_income": False,
                "growth_to_income_count": 0,
                "single_to_etf_count": 0,
                "avg_execution_gap_percent": 0.0,
                "sample_size": 0,
                "personalization_confidence": "Low",
                "adjustment_strength": 0.0,
                "under_deployer": False,
            }

        deploy_ratios: list[float] = []
        deploy_ratio_weights: list[float] = []
        execution_gaps: list[float] = []
        skip_count = 0
        replace_count = 0
        decision_count = 0
        replacement_events = 0
        etf_replacements = 0
        income_replacements = 0
        growth_to_income_count = 0
        single_to_etf_count = 0

        total_rows = len(rows)
        for idx, row in enumerate(rows):
            delta = row.get("decision_delta") if isinstance(row.get("decision_delta"), dict) else {}
            rec_total = _to_float(delta.get("total_recommended"))
            actual_total = _to_float(delta.get("total_actual"))
            if rec_total > 0:
                deploy_ratios.append(max(0.0, actual_total / rec_total))
                # Rows are ordered newest->oldest. Weight recent logs higher.
                deploy_ratio_weights.append(float(total_rows - idx))
            execution_gaps.append(_to_float(row.get("execution_gap_percent")))

            actuals = row.get("actual_decisions")
            decisions = actuals if isinstance(actuals, list) else []
            for decision in decisions:
                if not isinstance(decision, dict):
                    continue
                decision_count += 1
                action = str(decision.get("actual_action") or "").strip().upper()
                replacement_ticker = str(decision.get("replacement_ticker") or "").strip().upper()
                reason_blob = str(decision.get("reason") or "").lower()
                if action == "SKIPPED":
                    skip_count += 1
                if action == "REPLACED" or replacement_ticker:
                    replace_count += 1
                    replacement_events += 1
                    if replacement_ticker and _is_etf_like(replacement_ticker):
                        etf_replacements += 1
                    if replacement_ticker in INCOME_ETF_HINTS or any(
                        phrase in reason_blob for phrase in ("income", "dividend", "yield")
                    ):
                        income_replacements += 1

            category_shift = delta.get("category_shift") if isinstance(delta.get("category_shift"), dict) else {}
            if bool(category_shift.get("growth_to_income")) or str(row.get("style_shift") or "") == "growth_to_income":
                growth_to_income_count += 1
            if bool(category_shift.get("single_to_etf")):
                single_to_etf_count += 1

        total_decisions = max(1, decision_count)
        avg_deploy_ratio = sum(deploy_ratios) / len(deploy_ratios) if deploy_ratios else 1.0
        stable_deploy_ratio = _weighted_median(deploy_ratios, deploy_ratio_weights) if deploy_ratios else 1.0
        skip_rate = skip_count / total_decisions
        replace_rate = replace_count / total_decisions
        prefers_etf = replacement_events > 0 and (etf_replacements / replacement_events) > 0.50
        prefers_income = income_replacements >= 2 or growth_to_income_count >= 2
        avg_execution_gap = sum(execution_gaps) / len(execution_gaps) if execution_gaps else 0.0
        sample_size = len(rows)

        if sample_size < 3:
            confidence_label = "Low"
            adjustment_strength = 0.0
        elif sample_size <= 5:
            confidence_label = "Medium"
            adjustment_strength = 0.5
        else:
            confidence_label = "High"
            adjustment_strength = 1.0

        return {
            "avg_deploy_ratio": round(avg_deploy_ratio, 4),
            "stable_deploy_ratio": round(stable_deploy_ratio, 4),
            "skip_rate": round(skip_rate, 4),
            "replace_rate": round(replace_rate, 4),
            "prefers_etf": prefers_etf,
            "prefers_income": prefers_income,
            "growth_to_income_count": growth_to_income_count,
            "single_to_etf_count": single_to_etf_count,
            "avg_execution_gap_percent": round(avg_execution_gap, 2),
            "sample_size": sample_size,
            "personalization_confidence": confidence_label,
            "adjustment_strength": adjustment_strength,
            "under_deployer": stable_deploy_ratio < 0.85,
        }
