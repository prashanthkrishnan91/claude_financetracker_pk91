"""Decision log service — persists and retrieves deploy-plan decisions."""

from __future__ import annotations

import asyncio
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
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


def _to_optional_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    if isinstance(value, str):
        try:
            numeric = float(value)
        except ValueError:
            return None
        return numeric if math.isfinite(numeric) else None
    return None


def _is_etf_like(ticker: str) -> bool:
    return ticker.upper() in ETF_HINTS


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


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


def _window_status(days_elapsed: float, window_days: int, has_any_data: bool) -> str:
    if days_elapsed < window_days:
        return "pending"
    return "ready" if has_any_data else "unavailable"


def _is_near_zero(value: float, tolerance: float = 0.05) -> bool:
    return abs(value) <= tolerance


def _to_iso_utc(ts: float | None = None) -> str:
    if ts is None:
        return datetime.now(timezone.utc).isoformat()
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _parse_iso_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _derive_baseline_captured_at(snapshot: dict[str, Any]) -> str:
    meta = snapshot.get("_meta") if isinstance(snapshot.get("_meta"), dict) else {}
    explicit = str(meta.get("baseline_captured_at") or "").strip() if isinstance(meta, dict) else ""
    if explicit:
        return explicit
    candidates: list[datetime] = []
    for key, value in snapshot.items():
        if str(key).startswith("_") or not isinstance(value, dict):
            continue
        parsed = _parse_iso_utc(value.get("timestamp"))
        if parsed is not None:
            candidates.append(parsed)
    if candidates:
        return min(candidates).isoformat()
    return _to_iso_utc()


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

    def _price_snapshot_has_tickers(self, snapshot: dict[str, Any]) -> bool:
        for key, value in snapshot.items():
            if key.startswith("_"):
                continue
            if isinstance(value, dict):
                return True
        return False

    def _is_missing_price(self, price_value: float) -> bool:
        return (not math.isfinite(price_value)) or price_value <= 0.0

    def create(self, user_id: str, data: dict[str, Any]) -> dict[str, Any]:
        recommendation_snapshot = data.get("recommendation_snapshot")
        actual_decisions = data.get("actual_decisions")
        analysis = analyzeDecisionDelta(
            recommendation_snapshot=recommendation_snapshot,
            actual_decisions=actual_decisions,
        )
        tickers_for_snapshot = self._extract_recommendation_tickers(recommendation_snapshot) + self._extract_replacement_tickers(actual_decisions)
        price_snapshot = _run_async(self._fetch_price_map_async(tickers_for_snapshot))
        if isinstance(price_snapshot, dict):
            price_snapshot.setdefault("_meta", {})
            if isinstance(price_snapshot["_meta"], dict):
                price_snapshot["_meta"].setdefault("backfilled", False)
                price_snapshot["_meta"].setdefault("baseline_captured_at", _to_iso_utc())
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
        unique_tickers_needed = [t for t in sorted(set(tickers_needed)) if t]

        existing_has_prices = self._price_snapshot_has_tickers(existing_snapshot)
        snapshot_backfilled = not existing_has_prices
        fetched_backfill = _run_async(self._fetch_price_map_async(unique_tickers_needed)) if snapshot_backfilled else {}
        missing_tickers = [t for t in unique_tickers_needed if t not in existing_snapshot]
        fetched_missing = _run_async(self._fetch_price_map_async(missing_tickers)) if (not snapshot_backfilled and missing_tickers) else {}
        merged_price_snapshot = {**existing_snapshot, **fetched_backfill, **fetched_missing}
        snapshot_meta = merged_price_snapshot.get("_meta") if isinstance(merged_price_snapshot.get("_meta"), dict) else {}
        if not isinstance(snapshot_meta, dict):
            snapshot_meta = {}
        if snapshot_backfilled:
            snapshot_meta["backfilled"] = True
            snapshot_meta["backfilled_at"] = _to_iso_utc()
        else:
            snapshot_meta.setdefault("backfilled", bool(snapshot_meta.get("backfilled")))
        snapshot_meta.setdefault("baseline_captured_at", _derive_baseline_captured_at(merged_price_snapshot))
        merged_price_snapshot["_meta"] = snapshot_meta

        current_prices = _run_async(self._fetch_price_map_async(unique_tickers_needed))

        per_ticker: list[dict[str, Any]] = []
        data_quality: list[dict[str, Any]] = []
        rec_weighted_sum = 0.0
        rec_weight_total = 0.0
        actual_weighted_sum = 0.0
        actual_weight_total = 0.0
        skipped_count = 0
        replaced_count = 0
        amount_mismatch_found = False

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

            actual_decision = actual_by_ticker.get(ticker) or {}
            action = str(actual_decision.get("actual_action") or "BOUGHT").strip().upper()
            if action == "SKIPPED":
                skipped_count += 1
            if action == "REPLACED":
                replaced_count += 1
            actual_ticker = ticker
            if action == "REPLACED":
                replacement = str(actual_decision.get("replacement_ticker") or "").strip().upper()
                if replacement:
                    actual_ticker = replacement
            actual_amount = _to_float(actual_decision.get("actual_amount"))
            exposure_amount = 0.0 if action == "SKIPPED" else (actual_amount if actual_amount > 0 else recommended_amount)
            if action != "SKIPPED" and recommended_amount > 0 and exposure_amount > 0 and abs(exposure_amount - recommended_amount) > 0.01:
                amount_mismatch_found = True
            actual_entry = merged_price_snapshot.get(actual_ticker) if isinstance(merged_price_snapshot.get(actual_ticker), dict) else {}
            actual_now = current_prices.get(actual_ticker) if isinstance(current_prices.get(actual_ticker), dict) else {}
            actual_entry_price = _to_float(actual_entry.get("price"))
            actual_current_price = _to_float(actual_now.get("price"))

            row_status = "ok"
            row_reason = None
            if self._is_missing_price(rec_entry_price) or self._is_missing_price(rec_current_price):
                row_status = "missing_price"
                row_reason = "Missing entry price/current price"
                data_quality.append({"status": row_status, "reason": row_reason, "ticker": ticker, "leg": "recommended"})
            if exposure_amount > 0 and (self._is_missing_price(actual_entry_price) or self._is_missing_price(actual_current_price)):
                row_status = "missing_price"
                row_reason = "Missing entry price/current price"
                data_quality.append({"status": row_status, "reason": row_reason, "ticker": actual_ticker, "leg": "actual", "for_ticker": ticker})

            recommended_return_pct: float | None = None
            actual_return_pct: float | None = None
            delta_pct: float | None = None
            if row_status == "ok":
                recommended_return_pct = _pct_return(rec_entry_price, rec_current_price)
                actual_return_pct = 0.0 if exposure_amount <= 0 else _pct_return(actual_entry_price, actual_current_price)
                delta_pct = actual_return_pct - recommended_return_pct

            per_ticker.append(
                {
                    "ticker": f"{ticker} → {actual_ticker}" if action == "REPLACED" and actual_ticker != ticker else ticker,
                    "recommended_ticker": ticker,
                    "actual_ticker": actual_ticker,
                    "actual_action": action,
                    "status": row_status,
                    "reason": row_reason,
                    "recommended_return_pct": round(recommended_return_pct, 4) if recommended_return_pct is not None else None,
                    "actual_return_pct": round(actual_return_pct, 4) if actual_return_pct is not None else None,
                    "delta_pct": round(delta_pct, 4) if delta_pct is not None else None,
                }
            )

            if row_status == "ok" and recommended_amount > 0 and recommended_return_pct is not None:
                rec_weighted_sum += recommended_return_pct * recommended_amount
                rec_weight_total += recommended_amount
            if row_status == "ok" and exposure_amount > 0 and actual_return_pct is not None:
                actual_weighted_sum += actual_return_pct * exposure_amount
                actual_weight_total += exposure_amount

        total_recommended = rec_weighted_sum / rec_weight_total if rec_weight_total > 0 else 0.0
        total_actual = actual_weighted_sum / actual_weight_total if actual_weight_total > 0 else 0.0
        total_delta = total_actual - total_recommended

        comparable_rows = [item for item in per_ticker if item.get("status") == "ok" and isinstance(item.get("delta_pct"), (int, float))]
        best = max(comparable_rows, key=lambda item: item["delta_pct"], default=None)
        worst = min(comparable_rows, key=lambda item: item["delta_pct"], default=None)

        evaluated_at = _to_iso_utc()
        evaluated_dt = _parse_iso_utc(evaluated_at) or datetime.now(timezone.utc)
        baseline_captured_at = str(snapshot_meta.get("baseline_captured_at") or "").strip() or evaluated_at
        baseline_dt = _parse_iso_utc(baseline_captured_at) or evaluated_dt
        less_than_one_trading_day = (evaluated_dt - baseline_dt) < timedelta(days=1)
        tiny_moves = (
            bool(comparable_rows)
            and all(
                abs(float(item.get("recommended_return_pct") or 0.0)) < 0.01
                and abs(float(item.get("actual_return_pct") or 0.0)) < 0.01
                and abs(float(item.get("delta_pct") or 0.0)) < 0.01
                for item in comparable_rows
            )
        )
        equal_entry_and_current = (
            bool(comparable_rows)
            and all(
                _is_near_zero(float(item.get("recommended_return_pct") or 0.0), tolerance=0.0001)
                and _is_near_zero(float(item.get("actual_return_pct") or 0.0), tolerance=0.0001)
                for item in comparable_rows
            )
        )
        baseline_guard = bool(snapshot_meta.get("backfilled")) or less_than_one_trading_day or tiny_moves or equal_entry_and_current
        has_missing_rows = any(item.get("status") == "missing_price" for item in per_ticker)
        has_any_rows = bool(per_ticker)
        has_any_comparable = bool(comparable_rows)

        if not comparable_rows:
            evaluation_status = "missing_price"
        elif baseline_guard:
            evaluation_status = "baseline_captured"
        elif has_missing_rows:
            evaluation_status = "partial_data"
        else:
            evaluation_status = "ready"

        days_elapsed = max(0.0, (evaluated_dt - baseline_dt).total_seconds() / 86400.0)
        windows: dict[str, Any] = {}
        for window_days in (7, 30, 90):
            key = f"{window_days}d"
            window_eval_status = _window_status(days_elapsed, window_days, has_any_comparable)
            if not has_any_rows:
                window_eval_status = "insufficient_data"
            windows[key] = {
                "status": window_eval_status,
                "recommended_return_pct": round(total_recommended, 4) if window_eval_status == "ready" else None,
                "actual_return_pct": round(total_actual, 4) if window_eval_status == "ready" else None,
                "delta_pct": round(total_delta, 4) if window_eval_status == "ready" else None,
                "as_of": evaluated_at,
            }

        matched_model = (
            evaluation_status == "ready"
            and skipped_count == 0
            and replaced_count == 0
            and not amount_mismatch_found
            and abs(total_delta) < 0.05
        )
        if evaluation_status == "ready":
            if abs(total_delta) < 0.05:
                summary_text = "You matched the model"
            elif total_delta > 0.05:
                summary_text = f"You outperformed the model by {round(total_delta, 2):.2f}%"
            else:
                summary_text = f"You underperformed the model by {abs(round(total_delta, 2)):.2f}%"
        elif evaluation_status == "baseline_captured":
            summary_text = "Performance baseline captured. Return comparison will become meaningful after prices move."
        elif evaluation_status == "partial_data":
            summary_text = "Partial data: some tickers are missing required price points."
        else:
            summary_text = "Missing price data: return comparison is not available yet."

        performance_snapshot = {
            "status": evaluation_status,
            "evaluated_at": evaluated_at,
            "baseline_captured_at": baseline_captured_at,
            "portfolio": {
                "recommended_return": round(total_recommended, 4),
                "actual_return": round(total_actual, 4),
                "delta": round(total_delta, 4),
                "total_recommended_return": round(total_recommended, 4),
                "total_actual_return": round(total_actual, 4),
                "total_delta": round(total_delta, 4),
                "best_decision": best,
                "worst_decision": worst,
                "matched_model": matched_model,
                "too_early_to_judge": evaluation_status == "baseline_captured",
                "backfilled_baseline": bool(snapshot_meta.get("backfilled")),
                "summary_text": summary_text,
            },
            "windows": windows,
            "per_ticker": per_ticker,
            "data_quality": data_quality,
        }

        updated = (
            self.client.table("decision_logs")
            .update(
                {
                    "price_snapshot": merged_price_snapshot,
                    "performance_snapshot": performance_snapshot,
                    "evaluated_at": performance_snapshot["evaluated_at"],
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

    def getDecisionPerformanceInsights(self, user_id: str, limit: int = 20) -> dict[str, Any]:
        rows = self.list(user_id=user_id, limit=limit)
        total_logs = len(rows)
        eligible_rows: list[dict[str, Any]] = []
        all_messages: list[str] = []

        for row in rows:
            perf = row.get("performance_snapshot") if isinstance(row.get("performance_snapshot"), dict) else {}
            if str(perf.get("status") or "").strip().lower() != "ready":
                continue
            portfolio = perf.get("portfolio") if isinstance(perf.get("portfolio"), dict) else {}
            delta = _to_optional_float(portfolio.get("delta"))
            actual_return = _to_optional_float(portfolio.get("actual_return"))
            model_return = _to_optional_float(portfolio.get("recommended_return"))
            if delta is None or actual_return is None or model_return is None:
                continue
            eligible_rows.append(row)

        eligible_logs = len(eligible_rows)
        if eligible_logs <= 2:
            confidence = "low"
        elif eligible_logs <= 7:
            confidence = "medium"
        else:
            confidence = "high"

        portfolio_actual_returns: list[float] = []
        portfolio_model_returns: list[float] = []
        portfolio_deltas: list[float] = []
        wins = 0

        replacement_deltas: list[float] = []
        replacement_wins = 0
        replacement_count = 0
        etf_replacement_deltas: list[float] = []
        etf_replacement_wins = 0
        etf_replacement_count = 0
        skipped_count = 0
        under_deployment_deltas: list[float] = []
        under_deployment_count = 0

        best_override: dict[str, Any] | None = None
        worst_override: dict[str, Any] | None = None

        for row in eligible_rows:
            perf = row.get("performance_snapshot") if isinstance(row.get("performance_snapshot"), dict) else {}
            portfolio = perf.get("portfolio") if isinstance(perf.get("portfolio"), dict) else {}
            delta = float(_to_optional_float(portfolio.get("delta")) or 0.0)
            actual_return = float(_to_optional_float(portfolio.get("actual_return")) or 0.0)
            model_return = float(_to_optional_float(portfolio.get("recommended_return")) or 0.0)

            portfolio_actual_returns.append(actual_return)
            portfolio_model_returns.append(model_return)
            portfolio_deltas.append(delta)
            if delta > 0.05:
                wins += 1

            row_actual_decisions = row.get("actual_decisions") if isinstance(row.get("actual_decisions"), list) else []
            skipped_count += sum(
                1
                for decision in row_actual_decisions
                if isinstance(decision, dict) and str(decision.get("actual_action") or "").strip().upper() == "SKIPPED"
            )

            deploy_ratio: float | None = None
            row_decision_delta = row.get("decision_delta") if isinstance(row.get("decision_delta"), dict) else {}
            rec_total = _to_optional_float(row_decision_delta.get("total_recommended"))
            actual_total = _to_optional_float(row_decision_delta.get("total_actual"))
            if rec_total is not None and rec_total > 0 and actual_total is not None:
                deploy_ratio = actual_total / rec_total
            if deploy_ratio is not None and deploy_ratio < 0.85:
                under_deployment_count += 1
                under_deployment_deltas.append(delta)

            per_ticker = perf.get("per_ticker") if isinstance(perf.get("per_ticker"), list) else []
            for per_row in per_ticker:
                if not isinstance(per_row, dict):
                    continue
                if str(per_row.get("status") or "ok").strip().lower() != "ok":
                    continue
                action = str(per_row.get("actual_action") or "").strip().upper()
                label = str(per_row.get("ticker") or "")
                is_replacement = action == "REPLACED" or "→" in label
                if not is_replacement:
                    continue
                row_delta = _to_optional_float(per_row.get("delta_pct"))
                if row_delta is None:
                    continue
                replacement_count += 1
                replacement_deltas.append(row_delta)
                if row_delta > 0.05:
                    replacement_wins += 1

                recommended_ticker = str(per_row.get("recommended_ticker") or "").strip().upper()
                actual_ticker = str(per_row.get("actual_ticker") or "").strip().upper()
                if _is_etf_like(actual_ticker) and actual_ticker and actual_ticker != recommended_ticker:
                    etf_replacement_count += 1
                    etf_replacement_deltas.append(row_delta)
                    if row_delta > 0.05:
                        etf_replacement_wins += 1

                candidate = {
                    "ticker": label or f"{recommended_ticker} → {actual_ticker}",
                    "delta_pct": round(row_delta, 4),
                    "actual_action": action or None,
                }
                if best_override is None or candidate["delta_pct"] > best_override["delta_pct"]:
                    best_override = candidate
                if worst_override is None or candidate["delta_pct"] < worst_override["delta_pct"]:
                    worst_override = candidate

        avg_actual_return = _avg(portfolio_actual_returns) or 0.0
        avg_model_return = _avg(portfolio_model_returns) or 0.0
        avg_delta = _avg(portfolio_deltas) or 0.0
        win_rate_vs_model = (wins / eligible_logs) if eligible_logs > 0 else 0.0

        replacements_avg_delta = _avg(replacement_deltas)
        replacements_win_rate = (replacement_wins / replacement_count) if replacement_count > 0 else None
        etf_replacements_avg_delta = _avg(etf_replacement_deltas)
        etf_replacements_win_rate = (etf_replacement_wins / etf_replacement_count) if etf_replacement_count > 0 else None
        under_deployment_avg_delta = _avg(under_deployment_deltas)

        if eligible_logs < 3:
            all_messages.append("Not enough evaluated decisions yet. Keep logging decisions and re-evaluate after prices move.")
        else:
            direction = "outperformed" if avg_delta >= 0 else "underperformed"
            all_messages.append(
                f"Across {eligible_logs} evaluated decisions, your actual decisions have {direction} the model by {abs(avg_delta):.2f}% on average."
            )
            if replacement_count > 0 and replacements_avg_delta is not None:
                rep_direction = "outperformed" if replacements_avg_delta >= 0 else "underperformed"
                all_messages.append(
                    f"Your replacements have {rep_direction} the model by {abs(replacements_avg_delta):.2f}% on average."
                )
            if skipped_count > 0:
                all_messages.append(
                    "Your skipped recommendations are being tracked; opportunity-cost deltas are not available yet."
                )

        return {
            "eligible_logs": eligible_logs,
            "total_logs": total_logs,
            "confidence": confidence,
            "summary": {
                "avg_actual_return": round(avg_actual_return, 4),
                "avg_model_return": round(avg_model_return, 4),
                "avg_delta": round(avg_delta, 4),
                "win_rate_vs_model": round(win_rate_vs_model, 4),
                "best_override": best_override,
                "worst_override": worst_override,
            },
            "behavior_insights": {
                "replacements": {
                    "count": replacement_count,
                    "avg_delta": round(replacements_avg_delta, 4) if replacements_avg_delta is not None else None,
                    "win_rate": round(replacements_win_rate, 4) if replacements_win_rate is not None else None,
                },
                "skipped": {
                    "count": skipped_count,
                    "avg_delta": None,
                    "win_rate": None,
                },
                "under_deployment": {
                    "count": under_deployment_count,
                    "avg_delta": round(under_deployment_avg_delta, 4) if under_deployment_avg_delta is not None else None,
                    "win_rate": None,
                },
                "etf_replacements": {
                    "count": etf_replacement_count,
                    "avg_delta": round(etf_replacements_avg_delta, 4) if etf_replacements_avg_delta is not None else None,
                    "win_rate": round(etf_replacements_win_rate, 4) if etf_replacements_win_rate is not None else None,
                },
            },
            "messages": all_messages,
        }

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
