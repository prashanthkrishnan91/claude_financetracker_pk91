"""Stage 12C — Conservative Allocation Policy v1.

Read-only. No writes. No provider calls. No LLM calls.
Generates conservative target weights from Stage 11-certified portfolio truth
and produces a deterministic next-buy diagnostic for a given cash amount.

Policy: ETF floor 40%, individual stock cap 20%, speculative cap 5%,
crypto total cap 5%, alternatives total cap 5%, same-theme cap 40%.
Intel v3 conviction overlay is optional — endpoint degrades gracefully
without it using neutral defaults.

Stage 12C adds a deterministic core ETF preference policy for broad_index_etf
candidates: VTI > VOO > SPY > QQQ. Preference order governs over raw gap size
so SPY's larger gap cannot displace an eligible underweight VTI.

Stage 13A extends this same policy (no new model, no new endpoint) with
evidence-aware gating for individual-stock candidates. A stock only becomes
an eligible buy candidate when Intel v3 evidence for that ticker is present,
fresh, carries a constructive (BUY) signal with sufficient evidence
confidence, and has no blocking evidence gaps — on top of the existing
concentration/cap checks. Missing or ambiguous evidence blocks the ticker
rather than defaulting to a fake pass; the ETF plan still runs on its own.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from . import policy_tickers

logger = logging.getLogger(__name__)

DIAGNOSTIC_VERSION = "allocation_policy_v1"
POLICY_VERSION = "conservative_profile_policy_v1"

# ── Stage 12C: Core ETF preference order for broad_index_etf group ────────────
# Preference governs within the group when group is underweight.
# VTI = broad total-US core; VOO/SPY = S&P 500 core; QQQ = growth/tech-tilted.
# Membership/order is configuration (app/policy_tickers.json), not code —
# parity with the historic hardcoded values is asserted in test_policy_tickers.py.
BROAD_INDEX_CORE_PREFERENCE_ORDER: list[str] = list(
    policy_tickers.broad_index_core_preference_order()
)

# Numeric rank: VTI=4, VOO=3, SPY=2, QQQ=1. Unknown tickers get 0.
_CORE_ETF_PREFERENCE_RANK: dict[str, int] = {
    ticker: len(BROAD_INDEX_CORE_PREFERENCE_ORDER) - i
    for i, ticker in enumerate(BROAD_INDEX_CORE_PREFERENCE_ORDER)
}

# ── Staleness / reconciliation thresholds (same as Stage 11A) ────────────────
SNAPSHOT_STALE_HOURS: int = 24
PRICE_STALE_BUSINESS_DAYS: int = 3
RECONCILIATION_CERTIFIED_PCT: float = 1.0
RECONCILIATION_DEGRADED_PCT: float = 5.0

# Supabase per-ticker query limit (avoids silent 1000-row cap truncation)
_PER_TICKER_PRICE_LIMIT: int = 10
_NEAR_ZERO: float = 1e-9

# ── Conservative policy constants (percentages of portfolio) ─────────────────
ETF_FLOOR_PCT: float = 40.0
INDIVIDUAL_STOCK_CAP_PCT: float = 20.0
SPECULATIVE_CAP_PCT: float = 5.0
CRYPTO_TOTAL_CAP_PCT: float = 5.0
ALTERNATIVES_TOTAL_CAP_PCT: float = 5.0
SAME_THEME_CAP_PCT: float = 40.0

# ETF floor breakdown targets (must sum to ≤ ETF_FLOOR_PCT)
_ETF_BROAD_TARGET_PCT: float = 25.0
_ETF_DIVIDEND_TARGET_PCT: float = 10.0
_ETF_INTERNATIONAL_TARGET_PCT: float = 5.0
_ETF_SECTOR_TARGET_PCT: float = 5.0   # allocated across all sector ETFs held

# ── Ticker classification ────────────────────────────────────────────────────

GROUP_BROAD_ETF = "broad_index_etf"
GROUP_DIVIDEND_ETF = "dividend_etf"
GROUP_INTERNATIONAL_ETF = "international_etf"
GROUP_SECTOR_ETF = "sector_etf"
GROUP_ALTERNATIVES = "alternatives"
GROUP_CRYPTO = "crypto"
GROUP_INDIVIDUAL_STOCK = "individual_stock"
GROUP_SPECULATIVE = "speculative"

# ETF groups (collectively form the ETF floor)
_ETF_GROUPS = {GROUP_BROAD_ETF, GROUP_DIVIDEND_ETF, GROUP_INTERNATIONAL_ETF, GROUP_SECTOR_ETF}

# Ticker membership is configuration (app/policy_tickers.json), not code.
_BROAD_ETF_TICKERS: frozenset[str] = policy_tickers.etf_group_tickers(GROUP_BROAD_ETF)
_DIVIDEND_ETF_TICKERS: frozenset[str] = policy_tickers.etf_group_tickers(GROUP_DIVIDEND_ETF)
_INTERNATIONAL_ETF_TICKERS: frozenset[str] = policy_tickers.etf_group_tickers(GROUP_INTERNATIONAL_ETF)
_SECTOR_ETF_TICKERS: frozenset[str] = policy_tickers.etf_group_tickers(GROUP_SECTOR_ETF)
_ALTERNATIVES_TICKERS: frozenset[str] = policy_tickers.alternatives_tickers()
_CRYPTO_TICKERS: frozenset[str] = policy_tickers.crypto_tickers()
# Known speculative / small-cap / new IPO tickers in this portfolio
_SPECULATIVE_TICKERS: frozenset[str] = policy_tickers.speculative_tickers()


def classify_ticker(ticker: str) -> tuple[str, bool]:
    """Return (group_name, is_unknown).

    is_unknown=True means the ticker was not in any known classification set
    and defaulted to individual_stock.
    """
    t = ticker.upper()
    if t in _CRYPTO_TICKERS:
        return GROUP_CRYPTO, False
    if t in _BROAD_ETF_TICKERS:
        return GROUP_BROAD_ETF, False
    if t in _DIVIDEND_ETF_TICKERS:
        return GROUP_DIVIDEND_ETF, False
    if t in _INTERNATIONAL_ETF_TICKERS:
        return GROUP_INTERNATIONAL_ETF, False
    if t in _SECTOR_ETF_TICKERS:
        return GROUP_SECTOR_ETF, False
    if t in _ALTERNATIVES_TICKERS:
        return GROUP_ALTERNATIVES, False
    if t in _SPECULATIVE_TICKERS:
        return GROUP_SPECULATIVE, False
    # Default: individual stock with warning
    return GROUP_INDIVIDUAL_STOCK, True


# ── Utility helpers ──────────────────────────────────────────────────────────

def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_dt(val: Any) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    try:
        s = str(val).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _hours_since(dt: datetime | None) -> float | None:
    if dt is None:
        return None
    now = _now_utc()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return round((now - dt).total_seconds() / 3600.0, 2)


def _parse_date(val: Any) -> date | None:
    if val is None:
        return None
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    try:
        return datetime.strptime(str(val)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _business_days_since(price_date: date, today: date) -> int:
    if today <= price_date:
        return 0
    count = 0
    d = price_date + timedelta(days=1)
    while d <= today:
        if d.weekday() < 5:
            count += 1
        d += timedelta(days=1)
    return count


def _pct_diff(a: float, b: float) -> float | None:
    if abs(b) < _NEAR_ZERO:
        return None
    return round(abs(a - b) / abs(b) * 100.0, 4)


def _round5(amount: float) -> float:
    """Round to nearest $5 per repo convention."""
    return round(round(amount / 5.0) * 5.0, 2)


def _floor5(amount: float) -> float:
    """Floor to nearest $5 — never rounds up, guarantees result <= amount."""
    return round(int(amount / 5.0) * 5.0, 2)


# ── Step 1: Load and validate open positions ─────────────────────────────────

def _load_open_positions(pos_rows: list[dict]) -> list[dict]:
    """Filter position rows to open positions (Stage 11A semantics)."""
    open_pos = []
    for r in pos_rows:
        ticker = r.get("ticker")
        shares = _safe_float(r.get("shares"))
        category = (r.get("category") or "").upper()
        if ticker and shares and shares > 0 and category != "SELL":
            open_pos.append({
                "ticker": ticker,
                "shares": shares,
                "category": r.get("category"),
            })
    return open_pos


# ── Step 2: Build latest price map from per-ticker price_history rows ────────

def _build_price_map(price_rows_by_ticker: dict[str, list[dict]]) -> dict[str, dict | None]:
    """Return {ticker: latest_price_row_or_None} from per-ticker price rows."""
    result: dict[str, dict | None] = {}
    for ticker, rows in price_rows_by_ticker.items():
        if not rows:
            result[ticker] = None
            continue
        # rows already ordered desc by price_date — first is latest
        best = None
        best_date = None
        for r in rows:
            pd = _parse_date(r.get("price_date"))
            if pd is not None:
                if best_date is None or pd > best_date:
                    best_date = pd
                    best = r
        result[ticker] = best
    return result


# ── Step 3: Compute current portfolio weights ────────────────────────────────

def _compute_portfolio(
    open_positions: list[dict],
    price_map: dict[str, dict | None],
) -> tuple[dict[str, dict], float, list[str], list[str], list[str]]:
    """Compute per-ticker market value and weights.

    Returns:
        holdings: {ticker: {value, weight_pct, shares, group, is_unknown, price, price_date}}
        total_mv: total market value
        missing_price_tickers: tickers with no price
        stale_price_tickers: tickers with stale price
        unknown_tickers: tickers defaulted to individual_stock
    """
    holdings: dict[str, dict] = {}
    total_mv = 0.0
    missing_price_tickers: list[str] = []
    stale_price_tickers: list[str] = []
    unknown_tickers: list[str] = []
    today = _now_utc().date()

    for pos in open_positions:
        ticker = pos["ticker"]
        shares = pos["shares"]
        price_row = price_map.get(ticker)

        if price_row is None:
            missing_price_tickers.append(ticker)
            mv = None
            price = None
            price_date = None
        else:
            price = _safe_float(price_row.get("close_price"))
            price_date_raw = price_row.get("price_date")
            price_date = _parse_date(price_date_raw)
            if price is None:
                missing_price_tickers.append(ticker)
                mv = None
            elif price_date and _business_days_since(price_date, today) > PRICE_STALE_BUSINESS_DAYS:
                stale_price_tickers.append(ticker)
                mv = shares * price  # use stale but warn
            else:
                mv = shares * price

        if mv is not None:
            total_mv += mv

        group, is_unknown = classify_ticker(ticker)
        if is_unknown:
            unknown_tickers.append(ticker)

        holdings[ticker] = {
            "ticker": ticker,
            "shares": shares,
            "price": price,
            "price_date": str(price_date) if price_date else None,
            "market_value": mv,
            "weight_pct": None,  # filled after total_mv
            "group": group,
            "is_unknown": is_unknown,
        }

    # Assign weights
    if total_mv > _NEAR_ZERO:
        for ticker, h in holdings.items():
            mv = h["market_value"]
            if mv is not None:
                h["weight_pct"] = round(mv / total_mv * 100.0, 4)

    return holdings, total_mv, missing_price_tickers, stale_price_tickers, unknown_tickers


# ── Step 4: Compute group weights ────────────────────────────────────────────

def _compute_group_weights(holdings: dict[str, dict], total_mv: float) -> dict[str, dict]:
    """Aggregate holdings into group totals."""
    group_mv: dict[str, float] = {}
    for h in holdings.values():
        mv = h["market_value"] or 0.0
        group = h["group"]
        group_mv[group] = group_mv.get(group, 0.0) + mv

    group_weights: dict[str, dict] = {}
    all_groups = {
        GROUP_BROAD_ETF, GROUP_DIVIDEND_ETF, GROUP_INTERNATIONAL_ETF,
        GROUP_SECTOR_ETF, GROUP_ALTERNATIVES, GROUP_CRYPTO,
        GROUP_INDIVIDUAL_STOCK, GROUP_SPECULATIVE,
    }
    for g in all_groups:
        mv = group_mv.get(g, 0.0)
        w = round(mv / total_mv * 100.0, 4) if total_mv > _NEAR_ZERO else 0.0
        group_weights[g] = {"market_value": round(mv, 2), "weight_pct": w}

    # Combined ETF weight
    etf_mv = sum(group_mv.get(g, 0.0) for g in _ETF_GROUPS)
    etf_pct = round(etf_mv / total_mv * 100.0, 4) if total_mv > _NEAR_ZERO else 0.0
    group_weights["_etf_total"] = {"market_value": round(etf_mv, 2), "weight_pct": etf_pct}

    return group_weights


# ── Step 5: Generate conservative policy targets ─────────────────────────────

def _generate_policy(group_weights: dict[str, dict]) -> dict[str, Any]:
    """Generate group-level target weight percentages from conservative policy."""
    etf_total_pct = group_weights.get("_etf_total", {}).get("weight_pct", 0.0)

    # ETF sub-group targets (always apply)
    targets = {
        GROUP_BROAD_ETF: _ETF_BROAD_TARGET_PCT,
        GROUP_DIVIDEND_ETF: _ETF_DIVIDEND_TARGET_PCT,
        GROUP_INTERNATIONAL_ETF: _ETF_INTERNATIONAL_TARGET_PCT,
        GROUP_SECTOR_ETF: _ETF_SECTOR_TARGET_PCT,
        GROUP_ALTERNATIVES: ALTERNATIVES_TOTAL_CAP_PCT,
        GROUP_CRYPTO: CRYPTO_TOTAL_CAP_PCT,
        # Individual stocks + speculative share the remaining budget
        # Individual stocks: up to (100 - ETF_FLOOR - CRYPTO_CAP - ALT_CAP) but capped per ticker
        GROUP_INDIVIDUAL_STOCK: max(0.0, 100.0 - ETF_FLOOR_PCT - CRYPTO_TOTAL_CAP_PCT - ALTERNATIVES_TOTAL_CAP_PCT - _ETF_DIVIDEND_TARGET_PCT),
        GROUP_SPECULATIVE: SPECULATIVE_CAP_PCT,
    }

    # Caps used (per ticker, not group)
    caps = {
        "individual_stock_per_ticker_max_pct": INDIVIDUAL_STOCK_CAP_PCT,
        "speculative_per_ticker_max_pct": SPECULATIVE_CAP_PCT,
        "crypto_total_max_pct": CRYPTO_TOTAL_CAP_PCT,
        "alternatives_total_max_pct": ALTERNATIVES_TOTAL_CAP_PCT,
        "etf_floor_min_pct": ETF_FLOOR_PCT,
        "same_theme_total_max_pct": SAME_THEME_CAP_PCT,
    }

    return {
        "policy_version": POLICY_VERSION,
        "etf_floor_pct": ETF_FLOOR_PCT,
        "current_etf_pct": round(etf_total_pct, 4),
        "etf_floor_met": etf_total_pct >= ETF_FLOOR_PCT,
        "group_targets": targets,
        "caps": caps,
    }


# ── Step 6: Compute per-group and per-ticker gaps ────────────────────────────

def _compute_gaps(
    holdings: dict[str, dict],
    group_weights: dict[str, dict],
    policy: dict[str, Any],
    total_mv: float,
    stock_evidence_map: dict[str, dict] | None = None,
    stale_price_tickers: "list[str] | set[str] | None" = None,
) -> tuple[dict[str, dict], dict[str, dict]]:
    """Compute target vs current gaps at group and ticker level.

    A ticker with a missing OR stale price is never eligible for new cash
    (product-recovery Blocker 1 — the canonical allocation-policy boundary,
    not a presentation-layer filter). Missing price already gates via
    `market_value is None`; `stale_price_tickers` closes the remaining gap —
    a stale-but-present price previously still computed a market value/weight
    and could itself be selected as a candidate.
    """
    group_targets = policy["group_targets"]
    stale_ticker_set = set(stale_price_tickers or [])

    group_gaps: dict[str, dict] = {}
    for group, target_pct in group_targets.items():
        current = group_weights.get(group, {}).get("weight_pct", 0.0)
        gap_pct = round(target_pct - current, 4)
        gap_dollars = round(gap_pct / 100.0 * total_mv, 2) if total_mv > _NEAR_ZERO else 0.0
        if gap_pct > 0.5:
            status = "under"
        elif gap_pct < -0.5:
            status = "over"
        else:
            status = "within"
        group_gaps[group] = {
            "current_weight_pct": round(current, 4),
            "target_weight_pct": round(target_pct, 4),
            "gap_pct": gap_pct,
            "gap_dollars": gap_dollars,
            "status": status,
        }

    ticker_gaps: dict[str, dict] = {}
    for ticker, h in holdings.items():
        group = h["group"]
        current_pct = h["weight_pct"] or 0.0

        # Determine per-ticker cap
        if group == GROUP_SPECULATIVE:
            per_ticker_cap = SPECULATIVE_CAP_PCT
        elif group in _ETF_GROUPS:
            per_ticker_cap = 100.0  # no per-ticker ETF cap; group target governs
        elif group == GROUP_CRYPTO:
            per_ticker_cap = CRYPTO_TOTAL_CAP_PCT  # crypto per-ticker cap = group cap
        elif group == GROUP_ALTERNATIVES:
            per_ticker_cap = ALTERNATIVES_TOTAL_CAP_PCT
        else:
            per_ticker_cap = INDIVIDUAL_STOCK_CAP_PCT

        # Policy/cap eligibility — computed independently of Intel v3 evidence.
        # This is deliberately a separate variable from the final
        # `ineligibility_reason`/`eligible_for_buy` below: for individual
        # stocks, a policy blocker here must never short-circuit evaluation
        # of the evidence gate (Stage 13C fix — see evidence_gate_passed).
        policy_ineligibility_reason: str | None = None
        if h["market_value"] is None:
            policy_ineligibility_reason = "no_price_available"
        elif ticker in stale_ticker_set:
            # Present but stale — a real market value/weight was computed
            # ("use stale but warn" in _compute_portfolio) but the price is
            # not current enough to base a new-cash dollar recommendation on.
            policy_ineligibility_reason = "stale_price_not_eligible_for_new_cash"
        elif current_pct >= per_ticker_cap:
            policy_ineligibility_reason = f"at_or_above_{group}_cap_{per_ticker_cap}pct"

        # For ETFs: also check if group target is met
        elif group in _ETF_GROUPS:
            group_gap_status = group_gaps.get(group, {}).get("status", "within")
            if group_gap_status == "over":
                policy_ineligibility_reason = f"etf_group_{group}_already_above_target"

        # For crypto: check group total
        elif group == GROUP_CRYPTO:
            crypto_current = group_weights.get(GROUP_CRYPTO, {}).get("weight_pct", 0.0)
            if crypto_current >= CRYPTO_TOTAL_CAP_PCT:
                policy_ineligibility_reason = "crypto_group_at_or_above_cap"

        # For alternatives: check group total
        elif group == GROUP_ALTERNATIVES:
            alt_current = group_weights.get(GROUP_ALTERNATIVES, {}).get("weight_pct", 0.0)
            if alt_current >= ALTERNATIVES_TOTAL_CAP_PCT:
                policy_ineligibility_reason = "alternatives_group_at_or_above_cap"

        # Individual-stock sleeve guardrail: block new-dollar candidates for
        # the whole individual_stock group once its current weight is at or
        # above its policy target. Compared directly against the group's
        # current weight/target (not `group_gaps[...]["status"] == "over"`,
        # whose >0.5pct-band "over"/"within" split would incorrectly pass a
        # sleeve sitting exactly at its target).
        elif group == GROUP_INDIVIDUAL_STOCK:
            stock_group_current_pct = group_weights.get(GROUP_INDIVIDUAL_STOCK, {}).get("weight_pct", 0.0)
            stock_group_target_pct = group_targets.get(GROUP_INDIVIDUAL_STOCK, 0.0)
            if _safe_float(stock_group_current_pct) is not None and stock_group_current_pct >= stock_group_target_pct:
                policy_ineligibility_reason = "individual_stock_group_above_target"

        # Stage 13A/13C: individual stocks are additionally gated on Intel v3
        # evidence — but this gate is now ALWAYS evaluated for every
        # individual-stock ticker, independent of any policy blocker above.
        # This lets the diagnostic distinguish an evidence-eligible BUY that
        # was blocked purely by allocation policy from a ticker that
        # independently fails its own evidence checks (HOLD/THIN/stale/
        # missing), instead of collapsing every overweight-sleeve ticker into
        # a single undifferentiated policy-blocked bucket.
        evidence_gate_codes: list[str] = []
        evidence_gate_passed: bool = True
        if group == GROUP_INDIVIDUAL_STOCK:
            evidence_gate_passed, evidence_gate_codes = _evaluate_stock_candidate_gate(
                ticker, stock_evidence_map or {}
            )

        # Final eligibility requires both gates: no policy blocker AND a
        # passed evidence gate. Passing evidence never overrides a policy
        # blocker, and a passed policy check never overrides failed evidence.
        if group == GROUP_INDIVIDUAL_STOCK:
            eligible = policy_ineligibility_reason is None and evidence_gate_passed
            if policy_ineligibility_reason is not None:
                # Frontend-compatible field: expose the primary policy reason
                # when policy-blocked, even if evidence also failed — the
                # independent evidence_gate_passed/evidence_gate_codes fields
                # below still carry the full picture.
                ineligibility_reason = policy_ineligibility_reason
            elif not evidence_gate_passed:
                ineligibility_reason = "evidence_gate_failed:" + ",".join(evidence_gate_codes)
            else:
                ineligibility_reason = None
        else:
            ineligibility_reason = policy_ineligibility_reason
            eligible = ineligibility_reason is None

        # Target weight for this ticker (group target / tickers in group)
        tickers_in_group = [t for t, hh in holdings.items() if hh["group"] == group]
        n_in_group = len(tickers_in_group) or 1
        group_target = group_targets.get(group, 0.0)
        ticker_target_pct = min(per_ticker_cap, group_target / n_in_group)

        ticker_gaps[ticker] = {
            "ticker": ticker,
            "group": group,
            "current_weight_pct": round(current_pct, 4),
            "target_weight_pct": round(ticker_target_pct, 4),
            "per_ticker_cap_pct": per_ticker_cap,
            "gap_pct": round(ticker_target_pct - current_pct, 4),
            "eligible_for_buy": eligible,
            "ineligibility_reason": ineligibility_reason,
            "policy_ineligibility_reason": policy_ineligibility_reason,
            "evidence_gate_passed": evidence_gate_passed,
            "is_unknown_ticker": h["is_unknown"],
            "evidence_gate_codes": evidence_gate_codes,
        }

    return group_gaps, ticker_gaps


# ── Step 7: Optional Intel v3 conviction overlay ─────────────────────────────

def _extract_intel_v3_cards(intel_snapshot: dict | None) -> list[dict]:
    """Extract the canonical list of Intel v3 holding cards from a snapshot.

    Production snapshots (see `snapshot_builder.build_snapshot`) serialize
    held cards under `current_holdings` — that is the canonical production
    card list. `cards` is accepted only as a documented legacy/test
    compatibility fallback for older snapshot shapes; it is never combined
    with `current_holdings` so cards are never duplicated. Returns an empty
    list only when neither key holds a non-empty list.

    Handles both the wrapped database-row shape (`{"payload": {...}, ...}`,
    as returned by a direct `intel_v3_snapshots` table query) and the
    unwrapped payload shape (as returned by
    `IntelV3Service.get_latest_snapshot()`).
    """
    if not intel_snapshot:
        return []
    payload = intel_snapshot.get("payload") or intel_snapshot
    current_holdings = payload.get("current_holdings")
    if current_holdings:
        return current_holdings
    cards = payload.get("cards")
    if cards:
        return cards
    return []


def _intel_v3_snapshot_fallback_timestamp(intel_snapshot: dict, payload: dict) -> datetime | None:
    """Snapshot-level freshness fallback used when a card has no `updated_at`.

    Order: `payload.generated_at` (production snapshot generation time), then
    `intel_snapshot.created_at` (the wrapped database row's write time, for
    wrapped/legacy rows). Returns None if neither exists — callers must fail
    closed on that.
    """
    return _parse_dt(payload.get("generated_at")) or _parse_dt(intel_snapshot.get("created_at"))


def _parse_intel_v3_overlay(
    intel_snapshot: dict | None,
) -> tuple[dict[str, str], bool, str | None]:
    """Extract per-ticker conviction from Intel v3 snapshot payload.

    Returns (conviction_map, overlay_used, warning).
    conviction_map: {ticker: "HIGH"|"MEDIUM"|"LOW"|"neutral"}
    """
    if intel_snapshot is None:
        return {}, False, "intel_v3_snapshot_unavailable: using neutral conviction defaults"

    cards = _extract_intel_v3_cards(intel_snapshot)
    if not cards:
        return {}, False, "intel_v3_snapshot_has_no_cards: using neutral conviction defaults"

    conviction_map: dict[str, str] = {}
    for card in cards:
        ticker = card.get("ticker") or card.get("symbol")
        action = (card.get("action") or "").upper()
        conviction = (card.get("conviction_level") or card.get("conviction") or "").upper()
        if ticker and action in ("BUY", "HOLD"):
            conviction_map[ticker] = conviction if conviction in ("HIGH", "MEDIUM", "LOW") else "neutral"

    if not conviction_map:
        return {}, False, "intel_v3_snapshot_has_no_buy_hold_cards: using neutral conviction defaults"

    return conviction_map, True, None


# ── Step 7b: Individual-stock evidence gate (Stage 13A) ──────────────────────
#
# Individual stocks may only become buy candidates when Intel v3 evidence for
# that ticker is fresh, constructive, and confident. This is additive to the
# existing concentration/cap checks in `_compute_gaps` — it never loosens
# them. Kept separate from `_parse_intel_v3_overlay` so ETF/crypto/
# speculative ranking (which already reuses that overlay) is untouched.
#
# Production evidence-band vocabulary: Intel v3 cards serialize the internal
# AxisBand.OK axis as the string "PARTIAL", not "OK" — see
# `_EVIDENCE_QUALITY_TO_BAND` in `snapshot_builder.py` (STRONG->"STRONG",
# OK->"PARTIAL", THIN/SUPPRESSED->"THIN") and the existing `_STRONG_PARTIAL`
# positive-band set in `intel_v3_service.py`. The positive stock evidence
# bands here are therefore STRONG and PARTIAL. "OK" is accepted only as a
# legacy alias (normalized to PARTIAL) in case an older snapshot shape is
# ever encountered — it is not the production card shape.

_STOCK_POSITIVE_EVIDENCE_BANDS: frozenset[str] = frozenset({"STRONG", "PARTIAL"})
STOCK_EVIDENCE_STALE_HOURS: int = SNAPSHOT_STALE_HOURS


def _normalize_evidence_band(raw: Any) -> str:
    """Normalize a card's evidence_band to the production vocabulary.

    Production cards serialize AxisBand.OK as "PARTIAL" (see
    `_EVIDENCE_QUALITY_TO_BAND` in snapshot_builder.py). "OK" is accepted
    here only as a legacy alias for "PARTIAL" — it is not what production
    snapshots actually write.
    """
    band = (raw or "").upper()
    if band == "OK":
        return "PARTIAL"
    return band


def _parse_intel_v3_stock_evidence(intel_snapshot: dict | None) -> dict[str, dict[str, Any]]:
    """Extract per-ticker evidence detail used to gate individual-stock candidates.

    Returns {ticker: {action, conviction, evidence_band, asset_type, flags,
    hours_since_update}}. A ticker absent from a real snapshot's cards (or a
    missing snapshot entirely) is treated by the gate below as insufficient
    evidence — this function never fabricates a positive default.

    Freshness uses the card's own `updated_at` when present. If a card omits
    it, this falls back to `payload.generated_at` (the production snapshot's
    own generation time), and then to the wrapped row's `created_at` for
    wrapped/legacy rows. If none of these exist, `hours_since_update` stays
    None and the gate below fails closed on `evidence_freshness_unknown` —
    this fallback never loosens the freshness requirement, only widens where
    the timestamp may come from.
    """
    if intel_snapshot is None:
        return {}
    payload = intel_snapshot.get("payload") or intel_snapshot
    cards = _extract_intel_v3_cards(intel_snapshot)
    snapshot_fallback_at = _intel_v3_snapshot_fallback_timestamp(intel_snapshot, payload)
    evidence: dict[str, dict[str, Any]] = {}
    for card in cards:
        ticker = card.get("ticker") or card.get("symbol")
        if not ticker:
            continue
        updated_at = _parse_dt(card.get("updated_at")) or snapshot_fallback_at
        evidence[ticker] = {
            "action": (card.get("action") or "").upper(),
            "conviction": (card.get("conviction") or card.get("conviction_level") or "").upper(),
            "evidence_band": _normalize_evidence_band(card.get("evidence_band")),
            "asset_type": (card.get("asset_type") or "").lower(),
            "flags": list(card.get("flags") or []),
            "hours_since_update": _hours_since(updated_at),
        }
    return evidence


def _evaluate_stock_candidate_gate(
    ticker: str,
    stock_evidence_map: dict[str, dict[str, Any]],
) -> tuple[bool, list[str]]:
    """Gate an individual-stock candidate on Intel v3 evidence.

    Returns (passes, blocked_reason_codes). Every check fails closed: missing
    or ambiguous evidence blocks the candidate instead of defaulting to a
    fake pass.
    """
    ev = stock_evidence_map.get(ticker)
    if ev is None:
        return False, ["evidence_missing_for_ticker"]

    codes: list[str] = []

    if ev["action"] != "BUY":
        codes.append("evidence_signal_not_constructive")

    if ev["evidence_band"] not in _STOCK_POSITIVE_EVIDENCE_BANDS:
        codes.append("evidence_confidence_insufficient")

    hours = ev.get("hours_since_update")
    if hours is None:
        codes.append("evidence_freshness_unknown")
    elif hours > STOCK_EVIDENCE_STALE_HOURS:
        codes.append("evidence_stale")

    if ev.get("flags"):
        codes.append("evidence_has_blocking_gaps")

    return (len(codes) == 0), codes


# ── Step 8: Rank buy candidates ──────────────────────────────────────────────

_CONVICTION_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "neutral": 0}
_GROUP_PRIORITY = {
    GROUP_BROAD_ETF: 10,
    GROUP_DIVIDEND_ETF: 9,
    GROUP_INTERNATIONAL_ETF: 8,
    GROUP_SECTOR_ETF: 7,
    GROUP_INDIVIDUAL_STOCK: 5,
    GROUP_ALTERNATIVES: 4,
    GROUP_SPECULATIVE: 3,
    GROUP_CRYPTO: 2,
}


def _rank_buy_candidates(
    ticker_gaps: dict[str, dict],
    group_gaps: dict[str, dict],
    holdings: dict[str, dict],
    conviction_map: dict[str, str],
    intel_overlay_used: bool,
    etf_floor_met: bool,
    stock_evidence_map: dict[str, dict] | None = None,
) -> list[dict]:
    """Build ranked list of buy candidates.

    Primary: ETF/diversification priority when ETF floor is not met.
    Secondary (broad_index_etf): core ETF preference order (VTI>VOO>SPY>QQQ).
    Tertiary: Intel v3 conviction if available.
    Quaternary: positive gap size.

    For broad_index_etf, preference order governs within the group — a larger
    SPY gap does not displace an eligible underweight VTI.
    """
    # Pre-compute eligible broad_index_etf candidate tickers for reason code context.
    eligible_broad_tickers: set[str] = {
        t for t, tg in ticker_gaps.items()
        if tg["eligible_for_buy"] and tg["gap_pct"] > 0 and tg["group"] == GROUP_BROAD_ETF
    }

    candidates = []
    for ticker, tg in ticker_gaps.items():
        if not tg["eligible_for_buy"]:
            continue
        gap_pct = tg["gap_pct"]
        if gap_pct <= 0:
            continue  # already at or above target

        group = tg["group"]
        group_priority = _GROUP_PRIORITY.get(group, 1)

        # Boost ETF group priority when ETF floor is not met
        if not etf_floor_met and group in _ETF_GROUPS:
            group_priority += 5

        conviction = conviction_map.get(ticker, "neutral")
        conviction_rank = _CONVICTION_RANK.get(conviction, 0)
        confidence = "policy_plus_intel" if intel_overlay_used and conviction != "neutral" else "policy_only"

        # ── Stage 12C: core ETF preference for broad_index_etf ───────────────
        core_preference_rank = 0
        selection_policy = "policy_v1"
        preference_rank: int | None = None
        preference_reason: str | None = None
        skipped_higher_preference_tickers: list[str] = []
        extra_reason_codes: list[str] = []

        if group == GROUP_BROAD_ETF:
            pref_rank = _CORE_ETF_PREFERENCE_RANK.get(ticker, 0)
            core_preference_rank = pref_rank
            selection_policy = "core_etf_preference_v1"
            preference_rank = pref_rank if pref_rank > 0 else None

            if pref_rank > 0:
                preference_reason = "preferred_core_broad_market_etf"
                extra_reason_codes.append("core_etf_preference")

                # Tickers with higher preference that are held but NOT eligible candidates
                for higher_ticker, higher_rank in _CORE_ETF_PREFERENCE_RANK.items():
                    if higher_rank <= pref_rank:
                        continue
                    if higher_ticker not in ticker_gaps:
                        continue  # not held in this portfolio
                    htg = ticker_gaps[higher_ticker]
                    if not htg["eligible_for_buy"] or htg["gap_pct"] <= 0:
                        skipped_higher_preference_tickers.append(higher_ticker)

                # Specific code when VTI is selected and SPY is also an eligible candidate
                if ticker == "VTI" and "SPY" in eligible_broad_tickers:
                    extra_reason_codes.append("preferred_vti_over_spy")
        # ─────────────────────────────────────────────────────────────────────

        # ── Stage 13A: evidence-aware transparency fields for individual stocks ──
        asset_type = "equity" if group == GROUP_INDIVIDUAL_STOCK else group
        candidate_source = "policy_v1"
        confidence_label = "policy_baseline"
        evidence_rank = 0

        if group == GROUP_INDIVIDUAL_STOCK:
            ev = (stock_evidence_map or {}).get(ticker) or {}
            evidence_band = ev.get("evidence_band", "")
            candidate_source = "evidence_aware_stock_ranking_v1"
            if evidence_band == "STRONG":
                confidence_label = "high_confidence_evidence"
                evidence_rank = 2
            elif evidence_band == "PARTIAL":
                confidence_label = "moderate_confidence_evidence"
                evidence_rank = 1
            extra_reason_codes.append("evidence_fresh_and_constructive")
        # ─────────────────────────────────────────────────────────────────────

        reason_codes = (
            _build_reason_codes(group, group_gaps, etf_floor_met) + extra_reason_codes
        )

        candidates.append({
            "ticker": ticker,
            "group": group,
            "current_weight_pct": tg["current_weight_pct"],
            "target_or_cap_weight_pct": tg["target_weight_pct"],
            "gap_pct": gap_pct,
            "gap_dollars": 0.0,  # filled after total_mv
            "classification": group,
            "conviction": conviction,
            "confidence": confidence,
            "reason_codes": reason_codes,
            "selection_policy": selection_policy,
            "preference_rank": preference_rank,
            "preference_reason": preference_reason,
            "skipped_higher_preference_tickers": skipped_higher_preference_tickers,
            "asset_type": asset_type,
            "candidate_source": candidate_source,
            "confidence_label": confidence_label,
            "_sort_key": (group_priority, core_preference_rank, conviction_rank, evidence_rank, gap_pct),
            "is_unknown_ticker": tg.get("is_unknown_ticker", False),
        })

    candidates.sort(key=lambda c: c["_sort_key"], reverse=True)
    return candidates


def _build_reason_codes(
    group: str,
    group_gaps: dict[str, dict],
    etf_floor_met: bool,
) -> list[str]:
    codes = []
    if group in _ETF_GROUPS and not etf_floor_met:
        codes.append("etf_floor_not_met")
    gg = group_gaps.get(group, {})
    if gg.get("status") == "under":
        codes.append(f"{group}_group_underweight")
    return codes or ["positive_gap"]


# ── Step 9: Cash allocation ──────────────────────────────────────────────────

def _allocate_cash(
    candidates: list[dict],
    total_mv: float,
    cash_to_deploy: float,
    min_trade_amount: float,
    max_positions: int,
) -> tuple[list[dict], float, float, int, str | None]:
    """Allocate cash to buy candidates, respecting constraints.

    Returns (allocated_candidates, allocated_cash, unallocated_cash, count, no_buy_reason).
    """
    if not candidates:
        return [], 0.0, cash_to_deploy, 0, "no_eligible_buy_candidates"

    allocated: list[dict] = []
    remaining = cash_to_deploy

    for c in candidates:
        if len(allocated) >= max_positions:
            break
        if remaining < min_trade_amount:
            break

        # Gap dollars based on current total_mv + cash being deployed
        new_total = total_mv + cash_to_deploy
        gap_pct = c["gap_pct"]
        gap_dollars = round(gap_pct / 100.0 * new_total, 2)

        # Allocate: min(gap, remaining), at least min_trade_amount
        alloc = min(gap_dollars, remaining)
        if alloc < min_trade_amount:
            continue

        # Floor to nearest $5 — never allocate more than remaining cash
        alloc_rounded = _floor5(alloc)
        if alloc_rounded < min_trade_amount:
            alloc_rounded = min_trade_amount
        if alloc_rounded > remaining:
            alloc_rounded = _floor5(remaining)
            if alloc_rounded < min_trade_amount:
                continue

        c = dict(c)
        c["dollar_amount"] = alloc_rounded
        c["gap_dollars"] = round(gap_dollars, 2)
        del c["_sort_key"]

        allocated.append(c)
        remaining -= alloc_rounded

    allocated_cash = round(cash_to_deploy - remaining, 2)
    no_buy_reason = None
    if not allocated:
        no_buy_reason = "min_trade_amount_not_met_for_any_candidate" if candidates else "no_eligible_buy_candidates"

    return allocated, allocated_cash, round(remaining, 2), len(allocated), no_buy_reason


# ── Step 10: Reconciliation gate ─────────────────────────────────────────────

def _check_reconciliation(
    snapshot_value: float | None,
    position_mv: float,
) -> tuple[str, list[str]]:
    """Return (reconciliation_status, blockers)."""
    if snapshot_value is None or position_mv is None or position_mv < _NEAR_ZERO:
        return "unavailable", ["no_usable_portfolio_value_for_reconciliation"]

    pct = _pct_diff(snapshot_value, position_mv)
    if pct is None:
        return "unavailable", ["division_by_zero_in_reconciliation"]

    if pct <= RECONCILIATION_CERTIFIED_PCT:
        return "pass", []
    elif pct <= RECONCILIATION_DEGRADED_PCT:
        return "degraded", []
    else:
        return "blocked", [
            f"reconciliation_blocked: snapshot={snapshot_value:.2f} vs "
            f"position_mv={position_mv:.2f} ({pct:.2f}%)"
        ]


# ── Main entry point ──────────────────────────────────────────────────────────

async def run_next_buy_policy_diagnostic(
    db_client: Any,
    user_id: str,
    cash_to_deploy: float,
    max_positions: int = 5,
    min_trade_amount: float = 25.0,
) -> dict[str, Any]:
    """Stage 12B — Conservative next-buy policy diagnostic.

    Read-only. No writes. No provider calls. No LLM calls.
    Cert-gated: caller must verify cert before invoking this function.

    Returns a deterministic next-buy plan for the supplied cash amount based
    on Stage 11-certified portfolio truth and conservative allocation policy.
    """
    generated_at = _now_utc().isoformat()
    warnings: list[str] = []

    # ── 1. Load latest portfolio snapshot ────────────────────────────────────
    snapshot_value: float | None = None
    snapshot_at_str: str | None = None
    try:
        snap_res = (
            db_client.table("portfolio_snapshots")
            .select("total_equity,snapshot_at")
            .eq("user_id", user_id)
            .order("snapshot_at", desc=True)
            .limit(1)
            .execute()
        )
        snap_rows = snap_res.data or []
        if snap_rows:
            snapshot_value = _safe_float(snap_rows[0].get("total_equity"))
            snapshot_at_str = str(snap_rows[0].get("snapshot_at") or "")
    except Exception as exc:
        warnings.append(f"snapshot_query_failed: {type(exc).__name__}")
        logger.warning("allocation_policy_v1 snapshot query failed: %s", exc)

    # ── 2. Load open positions ────────────────────────────────────────────────
    pos_rows: list[dict] = []
    try:
        pos_res = (
            db_client.table("positions")
            .select("ticker,shares,category")
            .eq("user_id", user_id)
            .gt("shares", 0)
            .neq("category", "SELL")
            .execute()
        )
        pos_rows = pos_res.data or []
    except Exception as exc:
        warnings.append(f"positions_query_failed: {type(exc).__name__}")
        logger.warning("allocation_policy_v1 positions query failed: %s", exc)

    open_positions = _load_open_positions(pos_rows)
    open_tickers = [p["ticker"] for p in open_positions]

    # ── 3. Load latest price per ticker (per-ticker queries, no bulk cap) ────
    price_map: dict[str, dict | None] = {}
    price_coverage_status = "ok"
    try:
        ticker_price_rows: dict[str, list[dict]] = {}
        for ticker in open_tickers:
            try:
                ph_res = (
                    db_client.table("price_history")
                    .select("ticker,price_date,close_price")
                    .eq("ticker", ticker)
                    .order("price_date", desc=True)
                    .limit(_PER_TICKER_PRICE_LIMIT)
                    .execute()
                )
                ticker_price_rows[ticker] = ph_res.data or []
            except Exception as exc:
                ticker_price_rows[ticker] = []
                warnings.append(f"price_query_failed_{ticker}: {type(exc).__name__}")
        price_map = _build_price_map(ticker_price_rows)
    except Exception as exc:
        warnings.append(f"price_history_query_failed: {type(exc).__name__}")
        logger.warning("allocation_policy_v1 price query failed: %s", exc)
        for ticker in open_tickers:
            price_map[ticker] = None

    # ── 4. Compute portfolio weights ──────────────────────────────────────────
    holdings, total_mv, missing_price_tickers, stale_price_tickers, unknown_tickers = (
        _compute_portfolio(open_positions, price_map)
    )

    if unknown_tickers:
        warnings.append(
            f"unknown_tickers_defaulted_to_individual_stock: {sorted(unknown_tickers)}"
        )

    # ── 5. Reconciliation check ───────────────────────────────────────────────
    recon_status, recon_blockers = _check_reconciliation(snapshot_value, total_mv)
    if recon_blockers:
        warnings.extend(recon_blockers)

    # ── 6. Determine Stage 11 truth dependency ────────────────────────────────
    has_missing_prices = len(missing_price_tickers) > 0
    has_stale_prices = len(stale_price_tickers) > 0

    if missing_price_tickers:
        price_coverage_status = "missing"
    elif stale_price_tickers:
        price_coverage_status = "stale"

    # Decide if policy can run
    policy_blockers: list[str] = list(recon_blockers)
    if total_mv < _NEAR_ZERO:
        policy_blockers.append("no_portfolio_value_computable")

    can_run_policy = len(policy_blockers) == 0

    # Degrade (not block) on stale/missing prices
    policy_status: str
    if policy_blockers:
        policy_status = "blocked"
    elif has_missing_prices or has_stale_prices:
        policy_status = "degraded"
        warnings.append("policy_degraded_due_to_price_coverage")
    else:
        policy_status = "ready"

    # ── 7. Optional Intel v3 overlay ──────────────────────────────────────────
    intel_snapshot: dict | None = None
    intel_overlay_used = False
    intel_warning: str | None = None
    try:
        intel_res = (
            db_client.table("intel_v3_snapshots")
            .select("payload,created_at")
            .eq("user_id", user_id)
            .eq("is_active", True)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        intel_rows = intel_res.data or []
        if intel_rows:
            intel_snapshot = intel_rows[0]
    except Exception as exc:
        intel_warning = f"intel_v3_query_failed: {type(exc).__name__}: using neutral conviction defaults"
        logger.info("allocation_policy_v1 intel_v3 query failed (non-blocking): %s", exc)

    conviction_map, intel_overlay_used, intel_conv_warning = _parse_intel_v3_overlay(intel_snapshot)
    if intel_conv_warning and not intel_warning:
        intel_warning = intel_conv_warning
    if intel_warning:
        warnings.append(intel_warning)

    # Stage 13A: separate, richer evidence extraction used only to gate
    # individual-stock candidates. Reuses the same intel_snapshot fetch above.
    stock_evidence_map = _parse_intel_v3_stock_evidence(intel_snapshot)

    # ── 8-10. Policy + gaps + candidates (only if can_run_policy) ─────────────
    group_weights = _compute_group_weights(holdings, total_mv) if can_run_policy else {}
    policy = _generate_policy(group_weights) if can_run_policy else {"policy_version": POLICY_VERSION}
    group_gaps: dict[str, dict] = {}
    ticker_gaps: dict[str, dict] = {}
    next_buy_candidates: list[dict] = []
    allocated_cash = 0.0
    unallocated_cash = cash_to_deploy
    allocation_count = 0
    no_buy_reason: str | None = None

    if can_run_policy:
        group_gaps, ticker_gaps = _compute_gaps(
            holdings, group_weights, policy, total_mv, stock_evidence_map,
            stale_price_tickers=stale_price_tickers,
        )
        etf_floor_met = policy.get("etf_floor_met", False)

        raw_candidates = _rank_buy_candidates(
            ticker_gaps, group_gaps, holdings,
            conviction_map, intel_overlay_used, etf_floor_met,
            stock_evidence_map,
        )
        next_buy_candidates, allocated_cash, unallocated_cash, allocation_count, no_buy_reason = (
            _allocate_cash(raw_candidates, total_mv, cash_to_deploy, min_trade_amount, max_positions)
        )

        # Defensive cash-bound invariant — must never trigger with correct allocator
        if allocated_cash > cash_to_deploy or unallocated_cash < 0:
            warnings.append(
                f"cash_bound_violated: allocated={allocated_cash:.2f} > cash_to_deploy={cash_to_deploy:.2f}"
            )
            policy_status = "degraded"
            unallocated_cash = max(0.0, round(cash_to_deploy - allocated_cash, 2))
            allocated_cash = min(allocated_cash, cash_to_deploy)
    else:
        no_buy_reason = "policy_blocked: " + "; ".join(policy_blockers)

    # ── Build current_portfolio section ──────────────────────────────────────
    per_ticker_summary = []
    for ticker, h in holdings.items():
        per_ticker_summary.append({
            "ticker": ticker,
            "market_value": round(h["market_value"], 2) if h["market_value"] is not None else None,
            "current_weight_pct": h["weight_pct"],
            "classification": h["group"],
            "is_unknown_ticker": h["is_unknown"],
            "price": h["price"],
            "price_date": h["price_date"],
            "shares": h["shares"],
        })
    per_ticker_summary.sort(key=lambda x: (x["market_value"] or 0.0), reverse=True)

    # ── Stage 13A: individual-stock candidate gating summary ─────────────────
    stock_tickers_held = [
        t for t, tg in ticker_gaps.items() if tg.get("group") == GROUP_INDIVIDUAL_STOCK
    ]
    stock_tickers_selected = [
        c["ticker"] for c in next_buy_candidates if c.get("asset_type") == "equity"
    ]
    # Stage 13C: policy eligibility and evidence eligibility are independent
    # gates for individual stocks (see _compute_gaps) — a ticker can fail
    # either, both, or neither. These four buckets report each combination
    # so the diagnostic never collapses an evidence-eligible-but-policy-
    # blocked BUY into the same bucket as a ticker that independently fails
    # its own evidence checks.
    stock_tickers_blocked: list[str] = []
    blocked_by_policy_tickers: list[str] = []
    evidence_eligible_but_policy_blocked_tickers: list[str] = []
    policy_block_reason_codes: dict[str, str] = {}
    for t in stock_tickers_held:
        if t in stock_tickers_selected:
            continue
        tg = ticker_gaps[t]
        has_policy_blocker = tg.get("policy_ineligibility_reason") is not None
        evidence_passed = bool(tg.get("evidence_gate_passed"))

        if has_policy_blocker:
            blocked_by_policy_tickers.append(t)
            policy_block_reason_codes[t] = tg["policy_ineligibility_reason"]
            if evidence_passed:
                evidence_eligible_but_policy_blocked_tickers.append(t)
        if not evidence_passed:
            stock_tickers_blocked.append(t)

    if stock_tickers_selected:
        stock_candidates_status = "enabled"
    elif evidence_eligible_but_policy_blocked_tickers:
        # At least one stock cleared evidence but every such stock was
        # policy-blocked — report the policy reason, not evidence-missing.
        stock_candidates_status = "blocked_by_policy_caps"
    elif stock_tickers_blocked:
        stock_candidates_status = "blocked_insufficient_evidence"
    elif blocked_by_policy_tickers:
        stock_candidates_status = "blocked_by_policy_caps"
    elif stock_tickers_held:
        stock_candidates_status = "blocked_by_policy_caps"
    else:
        stock_candidates_status = "no_stock_positions_held"

    # ── Assemble response ─────────────────────────────────────────────────────
    return {
        "diagnostic_version": DIAGNOSTIC_VERSION,
        "generated_at": generated_at,
        "input": {
            "cash_to_deploy": cash_to_deploy,
            "min_trade_amount": min_trade_amount,
            "max_positions": max_positions,
        },
        "truth_dependency": {
            "truth_status": "certified" if policy_status == "ready" else ("degraded" if policy_status == "degraded" else "blocked"),
            "reconciliation_status": recon_status,
            "snapshot_portfolio_value": round(snapshot_value, 2) if snapshot_value is not None else None,
            "snapshot_at": snapshot_at_str or None,
            "position_derived_market_value": round(total_mv, 2),
            "price_coverage_status": price_coverage_status,
            "missing_price_tickers": missing_price_tickers,
            "stale_price_tickers": stale_price_tickers,
            "can_run_policy": can_run_policy,
            "blockers": policy_blockers,
        },
        "current_portfolio": {
            "total_market_value": round(total_mv, 2),
            "open_position_count": len(open_positions),
            "per_ticker": per_ticker_summary,
            "group_weights": {
                k: v for k, v in group_weights.items() if not k.startswith("_")
            } if group_weights else {},
            "etf_total_weight_pct": group_weights.get("_etf_total", {}).get("weight_pct") if group_weights else None,
        },
        "generated_policy": {
            **policy,
            "intel_v3_overlay_used": intel_overlay_used,
            "intel_v3_overlay_warning": intel_warning,
            "warnings": warnings,
        },
        "target_vs_current": {
            "by_group": group_gaps,
            "by_ticker": ticker_gaps,
        },
        "next_buy_candidates": next_buy_candidates,
        "stock_candidates": {
            "status": stock_candidates_status,
            "held_tickers": stock_tickers_held,
            "selected_tickers": stock_tickers_selected,
            "blocked_by_evidence_tickers": stock_tickers_blocked,
            "blocked_by_policy_tickers": blocked_by_policy_tickers,
            "evidence_eligible_but_policy_blocked_tickers": evidence_eligible_but_policy_blocked_tickers,
            "policy_block_reason_codes": policy_block_reason_codes,
        },
        "cash_plan": {
            "cash_to_deploy": cash_to_deploy,
            "allocated_cash": allocated_cash,
            "unallocated_cash": unallocated_cash,
            "allocation_count": allocation_count,
            "no_buy_reason": no_buy_reason,
        },
        "verdict": {
            "policy_status": policy_status,
            "recommendations_trusted": False,
            "numeric_plan_trusted": (
                policy_status == "ready"
                and recon_status == "pass"
                and price_coverage_status == "ok"
                and not missing_price_tickers
                and not stale_price_tickers
                and not policy_blockers
                and allocated_cash <= cash_to_deploy
                and unallocated_cash >= 0
                and all(c.get("dollar_amount", 0) >= 0 for c in next_buy_candidates)
                and abs(
                    sum(c.get("dollar_amount", 0) for c in next_buy_candidates) - allocated_cash
                ) <= 0.02
            ),
            "next_required_fix": _next_fix(
                policy_status, policy_blockers, has_missing_prices,
                has_stale_prices, recon_status,
            ),
        },
    }


def _next_fix(
    policy_status: str,
    blockers: list[str],
    has_missing_prices: bool,
    has_stale_prices: bool,
    recon_status: str,
) -> str:
    if policy_status == "blocked":
        if any("reconciliation" in b for b in blockers):
            return "Reconcile portfolio values — snapshot vs position-derived diverges beyond tolerance"
        return "Resolve blockers: " + "; ".join(blockers)
    if has_missing_prices:
        return "Run Stage 11B current-price-truth-repair to fill missing price_history rows"
    if has_stale_prices:
        return "Run Stage 11B current-price-truth-repair to refresh stale price_history rows"
    if recon_status == "degraded":
        return "Reconciliation degraded — run Stage 11B current-price-truth-repair and refresh portfolio snapshot"
    return "No immediate fix required — policy is ready"
