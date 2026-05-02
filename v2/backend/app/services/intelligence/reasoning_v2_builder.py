"""Intel Reasoning v2 Builder — deterministic, pure, no IO.

Fuses deterministic scorecard data and analyst_verdict into one structured
reasoning object stored dormant in agent_runs.allocation["_reasoning_v2"].

Contract invariants:
  * Pure function: no IO, DB, network, randomness, datetime.now, or LLM calls.
  * Same inputs always produce byte-equivalent JSON output.
  * Accepts ScoreCard dataclass, serialized dict, or None.
  * Forbidden indicator language is scrubbed from all user_text fields.
  * No allocation math, dollar amounts, or position targets in output.
  * deploy_signals is metadata-only (conviction/risk/quality bands, blockers).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from ..reasoning_contract import _FORBIDDEN_PATTERNS, _contains_forbidden_language

SCHEMA_VERSION = "reasoning_v2.0"
VALID_GENERATION_VERSIONS = frozenset({"human_v2", "compact_v1"})
VALID_POSTURES = frozenset({"ACCUMULATE", "HOLD", "TRIM", "AVOID", "WATCH"})
VALID_SEVERITY = frozenset({"LOW", "MEDIUM", "HIGH", "UNKNOWN"})
VALID_CONVICTION_BANDS = frozenset({"HIGH", "MEDIUM", "LOW", "INSUFFICIENT_DATA"})
VALID_DATA_STATUSES = frozenset({"READY", "PARTIAL", "INSUFFICIENT_DATA"})

# Numeric subscore keys: maps scorecard dict key → evidence.deterministic key.
# Keep this list stable across PRs — removing entries breaks evidence traceability.
_SUBSCORE_KEY_MAP: tuple[tuple[str, str], ...] = (
    ("return_1d", "1d_return"),
    ("return_5d", "5d_return"),
    ("return_30d", "30d_return"),
    ("volatility_30d", "volatility_30d"),
    ("sentiment_score", "sentiment_score"),
    ("momentum_score", "momentum_score"),
    ("trend_regime", "trend_regime"),
    ("relative_strength", "relative_strength"),
)

# Thesis-engine subscore dimension keys: maps thesis ScoreCard subscore name →
# evidence.deterministic key. Only published (data_quality >= threshold) subscores
# are included. Keep this list stable — removing entries breaks traceability.
_THESIS_SUBSCORE_MAP: tuple[tuple[str, str], ...] = (
    ("quality", "quality_score"),
    ("valuation", "valuation_score"),
    ("growth", "growth_score"),
    ("risk", "risk_score"),
    ("momentum", "momentum_score"),
)

# Strings that indicate high-risk business situations (not technical indicators).
_HIGH_RISK_SIGNALS = ("halt", "bankruptcy", "collapse", "fraud", "delist", "liquidat")
_MED_RISK_SIGNALS = (
    "decline", "slowdown", "competition", "regulatory", "headwind",
    "elevated", "uncertainty", "pressure", "erosion",
)


# ── ScoreCard shape (forward-compatible stub for future thesis_engine) ───────


@dataclass
class ScoreCard:
    """Per-ticker deterministic thesis scorecard.

    This shape is a forward-compatible stub for future thesis_engine output.
    Callers in PR 1 pass None; this class is provided so the function
    signature stays stable when the scorecard pipeline materialises.
    """

    ticker: str
    status: str = "INSUFFICIENT_DATA"  # READY | PARTIAL | INSUFFICIENT_DATA
    data_quality_score: float = 0.0
    missing_fields: list[str] = field(default_factory=list)
    stale_fields: list[str] = field(default_factory=list)
    dimensions: dict[str, Any] = field(default_factory=dict)
    # Numeric signal dimensions (populated when status == READY or PARTIAL)
    return_1d: Optional[float] = None
    return_5d: Optional[float] = None
    return_30d: Optional[float] = None
    volatility_30d: Optional[float] = None
    sentiment_score: Optional[float] = None
    momentum_score: Optional[float] = None
    trend_regime: Optional[str] = None
    relative_strength: Optional[float] = None


# ── Internal helpers ─────────────────────────────────────────────────────────


def _scrub_forbidden(text: str) -> str:
    """Remove forbidden indicator patterns from text; collapse whitespace."""
    if not text:
        return text
    result = text
    for pattern in _FORBIDDEN_PATTERNS:
        result = pattern.sub("", result)
    return re.sub(r"\s{2,}", " ", result).strip()


def _safe_user_text(raw: str, max_len: int = 180) -> str:
    """Scrub forbidden language, truncate to max_len, strip trailing whitespace."""
    return _scrub_forbidden(raw)[:max_len].rstrip()


def _normalize_thesis_engine_scorecard(card: Any) -> dict[str, Any]:
    """Normalise a score_schema.ScoreCard (thesis engine output) to a plain dict.

    Detected via duck-typing: ``hasattr(card, 'quality') and hasattr(card, 'blended_data_quality')``.
    Keeps the same top-level key shape as ``_scorecard_to_dict()`` in the orchestrator
    so serialized thesis dicts and live ScoreCard objects produce identical evidence.
    """
    def _ss(ss: Any) -> dict:
        return {
            "score": float(getattr(ss, "score", 0.0)),
            "data_quality": float(getattr(ss, "data_quality", 0.0)),
            "published": bool(getattr(ss, "published", False)),
        }

    status_raw = getattr(card, "status", None)
    status_val = status_raw.value if hasattr(status_raw, "value") else str(status_raw or "INSUFFICIENT_DATA")
    band_raw = getattr(card, "conviction_band", None)
    band_val = band_raw.value if hasattr(band_raw, "value") else str(band_raw or "INSUFFICIENT_DATA")

    return {
        "ticker": str(getattr(card, "ticker", "") or ""),
        "status": status_val,
        "data_quality_score": float(getattr(card, "blended_data_quality", 0.0) or 0.0),
        "missing_fields": list(getattr(card, "inputs_missing", []) or []),
        "stale_fields": [],
        "conviction_score": getattr(card, "conviction_score", None),
        "conviction_band": band_val,
        "quality": _ss(getattr(card, "quality", None) or _EmptySubScore()),
        "valuation": _ss(getattr(card, "valuation", None) or _EmptySubScore()),
        "growth": _ss(getattr(card, "growth", None) or _EmptySubScore()),
        "risk": _ss(getattr(card, "risk", None) or _EmptySubScore()),
        "momentum": _ss(getattr(card, "momentum", None) or _EmptySubScore()),
    }


class _EmptySubScore:
    """Sentinel used by _normalize_thesis_engine_scorecard when a subscore is missing."""
    score = 0.0
    data_quality = 0.0
    published = False


def _normalize_scorecard(scorecard: Any) -> Optional[dict[str, Any]]:
    """Normalise ScoreCard object or dict to a plain dict, or None.

    Handles three input types in order:
    1. score_schema.ScoreCard (thesis engine output) — detected via duck-typing.
    2. Local ScoreCard stub (reasoning_v2_builder.ScoreCard).
    3. Plain dict (serialized scorecard from _scorecard_to_dict or caller).
    """
    if scorecard is None:
        return None
    # score_schema.ScoreCard has `quality` (SubScore) and `blended_data_quality`;
    # the local stub ScoreCard has neither of those attributes.
    if (
        not isinstance(scorecard, (ScoreCard, dict))
        and hasattr(scorecard, "quality")
        and hasattr(scorecard, "blended_data_quality")
    ):
        return _normalize_thesis_engine_scorecard(scorecard)
    if isinstance(scorecard, ScoreCard):
        return {
            "ticker": scorecard.ticker,
            "status": scorecard.status,
            "data_quality_score": scorecard.data_quality_score,
            "missing_fields": list(scorecard.missing_fields),
            "stale_fields": list(scorecard.stale_fields),
            "dimensions": dict(scorecard.dimensions),
            "return_1d": scorecard.return_1d,
            "return_5d": scorecard.return_5d,
            "return_30d": scorecard.return_30d,
            "volatility_30d": scorecard.volatility_30d,
            "sentiment_score": scorecard.sentiment_score,
            "momentum_score": scorecard.momentum_score,
            "trend_regime": scorecard.trend_regime,
            "relative_strength": scorecard.relative_strength,
        }
    if isinstance(scorecard, dict):
        return dict(scorecard)
    return None


def _sc_status(sc: Optional[dict]) -> str:
    if sc is None:
        return "INSUFFICIENT_DATA"
    raw = str(sc.get("status") or "INSUFFICIENT_DATA").upper().strip()
    return raw if raw in VALID_DATA_STATUSES else "INSUFFICIENT_DATA"


def _sc_quality(sc: Optional[dict]) -> float:
    if sc is None:
        return 0.0
    try:
        # Support both stub field (data_quality_score) and serialized thesis field (blended_data_quality).
        v = sc.get("data_quality_score") or sc.get("blended_data_quality") or 0.0
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return 0.0


def _analyst_is_usable(av: Optional[dict]) -> bool:
    """True when the analyst verdict passed validation (not fallback) and has a known schema."""
    if not isinstance(av, dict):
        return False
    gen = str(av.get("generation_version") or "").lower().strip()
    used_fallback = bool(av.get("used_fallback", False))
    return gen in VALID_GENERATION_VERSIONS and not used_fallback


def _conviction_band_from_analyst(av: Optional[dict]) -> str:
    """Derive conviction band from analyst verdict dict."""
    if not isinstance(av, dict):
        return "INSUFFICIENT_DATA"
    if bool(av.get("used_fallback", False)):
        return "INSUFFICIENT_DATA"
    action = str(av.get("action") or "").upper().strip()
    if action == "INSUFFICIENT_DATA":
        return "INSUFFICIENT_DATA"
    try:
        conviction = float(av.get("conviction") or 0.0)
    except (TypeError, ValueError):
        conviction = 0.0
    if conviction >= 0.65:
        return "HIGH"
    if conviction >= 0.35:
        return "MEDIUM"
    return "LOW"


def _data_quality_band(blended: float) -> str:
    if blended >= 0.75:
        return "HIGH"
    if blended >= 0.50:
        return "MEDIUM"
    return "LOW"


def _risk_band_from_analyst(av: Optional[dict]) -> str:
    """Derive risk band from analyst risk fields via keyword scan."""
    if not isinstance(av, dict):
        return "UNKNOWN"
    risk_parts = [
        str(av.get("risk_flag") or ""),
        str(av.get("risk") or ""),
    ]
    for r in (av.get("risks") or []):
        if isinstance(r, str):
            risk_parts.append(r)
    text = " ".join(risk_parts).lower()
    if not text.strip():
        return "UNKNOWN"
    for s in _HIGH_RISK_SIGNALS:
        if s in text:
            return "HIGH"
    for s in _MED_RISK_SIGNALS:
        if s in text:
            return "MEDIUM"
    return "LOW"


def _build_published_dimensions(sc: Optional[dict], status: str) -> list[str]:
    """Return evidence key names for deterministic subscores that have non-None values."""
    if sc is None or status == "INSUFFICIENT_DATA":
        return []
    published = []
    for sc_key, ev_key in _SUBSCORE_KEY_MAP:
        if sc.get(sc_key) is not None:
            published.append(ev_key)
    # Thesis-engine subscore dimensions (published flag must be True)
    for dim_key, ev_key in _THESIS_SUBSCORE_MAP:
        dim = sc.get(dim_key)
        if isinstance(dim, dict) and dim.get("published") and dim.get("score") is not None:
            published.append(ev_key)
    return published


def _derive_agreement(
    *,
    sc_status: str,
    analyst_is_usable: bool,
    analyst_action: str,
    sc: Optional[dict],
) -> str:
    """Return agreement label between deterministic signals and analyst."""
    if sc_status == "INSUFFICIENT_DATA" and not analyst_is_usable:
        return "insufficient"
    if sc_status == "INSUFFICIENT_DATA":
        return "analyst_only"
    if not analyst_is_usable:
        return "deterministic_only"

    # Both present: derive a simple scorecard direction.
    # Priority 1: thesis-engine conviction_band (HIGH/MEDIUM = positive, LOW = negative).
    # Priority 2: legacy stub fields (sentiment_score, return_30d).
    sc_direction: Optional[str] = None
    if sc is not None:
        conviction_band_val = str(sc.get("conviction_band") or "").upper().strip()
        if conviction_band_val in ("HIGH", "MEDIUM"):
            sc_direction = "positive"
        elif conviction_band_val == "LOW":
            sc_direction = "negative"
        # Fallback to legacy stub market-data fields when no conviction_band.
        if sc_direction is None:
            try:
                ss = float(sc.get("sentiment_score") or 0)
                if ss >= 0.2:
                    sc_direction = "positive"
                elif ss <= -0.2:
                    sc_direction = "negative"
            except (TypeError, ValueError):
                pass
        if sc_direction is None:
            try:
                r30 = float(sc.get("return_30d") or 0)
                if r30 > 5.0:
                    sc_direction = "positive"
                elif r30 < -5.0:
                    sc_direction = "negative"
            except (TypeError, ValueError):
                pass

    if sc_direction is None:
        return "agree"  # No directional signal from scorecard → no conflict

    analyst_bullish = analyst_action == "BUY"
    analyst_bearish = analyst_action in {"REDUCE", "SELL"}
    if (analyst_bullish and sc_direction == "positive") or (
        analyst_bearish and sc_direction == "negative"
    ):
        return "agree"
    if (analyst_bullish and sc_direction == "negative") or (
        analyst_bearish and sc_direction == "positive"
    ):
        return "disagree"
    return "agree"  # neutral pairs → no conflict


def _derive_posture(
    *,
    sc_status: str,
    analyst_is_usable: bool,
    analyst_action: str,
    agreement: str,
) -> str:
    """Derive ACCUMULATE|HOLD|TRIM|AVOID|WATCH from available signals."""
    if agreement == "disagree":
        return "WATCH"
    if sc_status == "INSUFFICIENT_DATA" and not analyst_is_usable:
        return "WATCH"
    if analyst_is_usable:
        mapping = {
            "BUY": "ACCUMULATE",
            "HOLD": "HOLD",
            "REDUCE": "TRIM",
            "INSUFFICIENT_DATA": "WATCH",
        }
        return mapping.get(analyst_action, "WATCH")
    # Scorecard present but no usable analyst
    return "WATCH"


def _enforce_insufficient_data_contract(
    *,
    sc_status: str,
    action_posture: str,
    deploy_action_posture: str,
    blockers: list[str],
    conviction_band: str,
) -> tuple[str, str, list[str], str]:
    """Force safe WATCH contract when deterministic data quality is insufficient."""
    if sc_status != "INSUFFICIENT_DATA":
        return action_posture, deploy_action_posture, blockers, conviction_band

    next_blockers = list(blockers)
    if "insufficient_data" not in next_blockers:
        next_blockers.append("insufficient_data")

    safe_conviction = (
        "INSUFFICIENT_DATA" if conviction_band == "HIGH" else conviction_band
    )
    if safe_conviction not in VALID_CONVICTION_BANDS:
        safe_conviction = "INSUFFICIENT_DATA"

    return "WATCH", "WATCH", next_blockers, safe_conviction


# ── Section builders ─────────────────────────────────────────────────────────


def _build_why(
    *,
    ticker: str,
    analyst_is_usable: bool,
    av: Optional[dict],
    sc_status: str,
    published_dimensions: list[str],
    evidence_deterministic: dict[str, Any],
) -> dict[str, Any]:
    user_text = ""
    support = "insufficient"
    subscore_basis: list[str] = []

    if analyst_is_usable and isinstance(av, dict):
        raw = str(
            av.get("primary_driver")
            or av.get("why")
            or av.get("why_this_matters")
            or ""
        )
        cleaned = _safe_user_text(raw, 180)
        if cleaned and not _contains_forbidden_language(cleaned):
            user_text = cleaned
            support = "analyst"

    if not user_text and sc_status == "PARTIAL" and published_dimensions:
        # Use deterministic dimensions as basis — all must exist in evidence
        subscore_basis = [k for k in published_dimensions[:3] if k in evidence_deterministic]
        count = len(published_dimensions)
        user_text = _safe_user_text(
            f"{ticker} shows partial signal coverage across {count} measured dimension(s); interpret with caution.",
            180,
        )
        support = "deterministic"

    if not user_text:
        user_text = _safe_user_text(
            f"Insufficient signal available for {ticker}. No clear edge identified from available data.",
            180,
        )
        support = "insufficient"

    return {"user_text": user_text, "support": support, "subscore_basis": subscore_basis}


def _build_risk(
    *,
    analyst_is_usable: bool,
    av: Optional[dict],
    sc_status: str,
) -> dict[str, Any]:
    user_text = ""
    severity = "UNKNOWN"
    support = "insufficient"

    if analyst_is_usable and isinstance(av, dict):
        raw = str(av.get("risk_flag") or av.get("risk") or "")
        cleaned = _safe_user_text(raw, 180)
        if cleaned and not _contains_forbidden_language(cleaned):
            user_text = cleaned
            support = "analyst"
            severity = _risk_band_from_analyst(av)

    if not user_text:
        if sc_status == "INSUFFICIENT_DATA":
            user_text = "Limited data reduces confidence. Avoid high-conviction sizing until signal improves."
        else:
            user_text = "Partial data coverage. Key risks cannot be fully assessed from available signals."
        user_text = _safe_user_text(user_text, 180)
        severity = "UNKNOWN"
        support = "insufficient"

    return {"user_text": user_text, "severity": severity, "support": support}


def _build_action(
    *,
    ticker: str,
    posture: str,
    analyst_is_usable: bool,
    av: Optional[dict],
    sc_status: str,
    agreement: str,
) -> dict[str, Any]:
    support = "deterministic"

    if posture == "WATCH":
        if agreement == "disagree":
            raw = f"Signal conflict detected for {ticker}. Watch until analyst and market data align."
        elif sc_status == "INSUFFICIENT_DATA" or not analyst_is_usable:
            raw = f"Insufficient data for {ticker}. Watch and wait for signal to strengthen."
        else:
            raw = f"Signals for {ticker} are inconclusive. Watch for clearer direction."
        return {"posture": "WATCH", "user_text": _safe_user_text(raw, 180), "support": support}

    if analyst_is_usable and isinstance(av, dict):
        raw = str(av.get("action_reason") or av.get("do") or av.get("what_to_do_now") or "")
        cleaned = _safe_user_text(raw, 180)
        if cleaned and not _contains_forbidden_language(cleaned):
            return {"posture": posture, "user_text": cleaned, "support": "analyst"}

    posture_templates = {
        "ACCUMULATE": f"Consider adding to {ticker} on weakness with disciplined position sizing.",
        "HOLD": f"Hold current {ticker} exposure and monitor for evidence changes.",
        "TRIM": f"Consider trimming {ticker} to manage risk and reduce concentration.",
        "AVOID": f"Avoid new {ticker} exposure until data quality improves.",
    }
    raw = posture_templates.get(posture, f"Review {ticker} with available evidence before acting.")
    return {"posture": posture, "user_text": _safe_user_text(raw, 180), "support": "deterministic"}


def _build_alt_view(
    *,
    analyst_is_usable: bool,
    av: Optional[dict],
) -> dict[str, Any]:
    if analyst_is_usable and isinstance(av, dict):
        raw = str(av.get("differentiation") or av.get("alt_view") or "")
        cleaned = _safe_user_text(raw, 180)
        if cleaned and cleaned != "—" and not _contains_forbidden_language(cleaned):
            return {"user_text": cleaned, "support": "analyst"}
    return {"user_text": "", "support": "insufficient"}


def _build_evidence(
    *,
    av: Optional[dict],
    sc: Optional[dict],
    sc_status: str,
    provider_meta: Optional[dict],
) -> dict[str, Any]:
    # Deterministic: only publish dimensions with actual values
    det: dict[str, Any] = {}
    if sc is not None and sc_status != "INSUFFICIENT_DATA":
        # Legacy market-data stub scorecard fields (return_*, sentiment_score, etc.)
        for sc_key, ev_key in _SUBSCORE_KEY_MAP:
            val = sc.get(sc_key)
            if val is not None:
                det[ev_key] = val
        # Thesis-engine subscore dimensions: include only published ones.
        # These are top-level keys in the serialized thesis dict (quality, valuation, …).
        for dim_key, ev_key in _THESIS_SUBSCORE_MAP:
            dim = sc.get(dim_key)
            if isinstance(dim, dict) and dim.get("published"):
                score = dim.get("score")
                if score is not None:
                    det[ev_key] = round(float(score), 1)

    # Analyst: pass-through only — no synthesis
    analyst_ev: dict[str, Any] = {}
    if isinstance(av, dict):
        analyst_ev = {
            "action": av.get("action"),
            "conviction": av.get("conviction"),
            "confidence": av.get("confidence"),
            "generation_version": av.get("generation_version"),
            "used_fallback": av.get("used_fallback"),
            "analysis_source": av.get("analysis_source"),
            "primary_driver": av.get("primary_driver") or av.get("why"),
            "risk_flag": av.get("risk_flag") or av.get("risk"),
            "action_reason": av.get("action_reason") or av.get("do"),
            "differentiation": av.get("differentiation") or av.get("alt_view"),
        }

    return {
        "deterministic": det,
        "analyst": analyst_ev,
        "provider": provider_meta if isinstance(provider_meta, dict) else None,
    }


def _build_data_quality(
    *,
    sc_status: str,
    sc: Optional[dict],
    sc_quality: float,
    analyst_is_usable: bool,
) -> dict[str, Any]:
    missing: list[str] = []
    stale: list[str] = []
    if sc is not None:
        missing = [str(m) for m in (sc.get("missing_fields") or []) if m]
        stale = [str(s) for s in (sc.get("stale_fields") or []) if s]

    # Blended quality: conservative average of scorecard and analyst signal presence
    if sc_quality > 0 and analyst_is_usable:
        # Analyst validated → treat as 0.80 quality signal
        blended = round((sc_quality + 0.80) / 2.0, 3)
    elif analyst_is_usable:
        blended = 0.60  # analyst present, no scorecard
    elif sc_quality > 0:
        blended = sc_quality
    else:
        blended = 0.0

    if sc_status == "INSUFFICIENT_DATA":
        note = "Data coverage is too thin to support confident signals for this ticker."
    elif sc_status == "PARTIAL":
        note = "Partial data available. Some signals may be missing or stale."
    else:
        note = "Data quality is sufficient for the signals shown."

    return {
        "status": sc_status,
        "blended_quality": blended,
        "missing": missing,
        "stale": stale,
        "user_safe_note": note,
    }


# ── Public API ───────────────────────────────────────────────────────────────


def build_reasoning_v2(
    *,
    ticker: str,
    scorecard: "ScoreCard | dict | None",
    analyst_verdict: Optional[dict],
    provider_meta: Optional[dict] = None,
) -> dict[str, Any]:
    """Build a deterministic reasoning_v2 object for a single ticker.

    Pure function — no IO, DB, network, randomness, datetime.now, or LLM calls.
    Same inputs always produce byte-equivalent dict output.

    Args:
        ticker: Ticker symbol (normalised to uppercase).
        scorecard: ScoreCard dataclass, serialised dict, or None.
            None produces an INSUFFICIENT_DATA envelope (safe default for PR 1).
        analyst_verdict: per_ticker_analyst AnalystVerdict.to_dict() or None.
            Only used when generation_version is compact_v1/human_v2 and
            used_fallback is False; otherwise recorded in evidence only.
        provider_meta: Optional provider / snapshot metadata (pass-through,
            not used for reasoning). Only stored in evidence.provider.

    Returns:
        Fully structured reasoning_v2 dict for dormant persistence in
        agent_runs.allocation["_reasoning_v2"][ticker].
    """
    ticker = str(ticker).strip().upper()
    sc = _normalize_scorecard(scorecard)
    av = analyst_verdict if isinstance(analyst_verdict, dict) else None

    sc_status = _sc_status(sc)
    sc_quality = _sc_quality(sc)
    analyst_usable = _analyst_is_usable(av)
    analyst_action = str((av or {}).get("action") or "INSUFFICIENT_DATA").upper().strip()

    # Build evidence first so subscore_basis can be verified against it
    evidence = _build_evidence(
        av=av,
        sc=sc,
        sc_status=sc_status,
        provider_meta=provider_meta,
    )
    published_dimensions = list(evidence["deterministic"].keys())

    agreement = _derive_agreement(
        sc_status=sc_status,
        analyst_is_usable=analyst_usable,
        analyst_action=analyst_action,
        sc=sc,
    )
    posture = _derive_posture(
        sc_status=sc_status,
        analyst_is_usable=analyst_usable,
        analyst_action=analyst_action,
        agreement=agreement,
    )

    conviction_band = _conviction_band_from_analyst(av)
    data_quality = _build_data_quality(
        sc_status=sc_status,
        sc=sc,
        sc_quality=sc_quality,
        analyst_is_usable=analyst_usable,
    )
    dq_band = _data_quality_band(data_quality["blended_quality"])
    risk_band = _risk_band_from_analyst(av) if analyst_usable else "UNKNOWN"

    # Deploy signal blockers and caveats
    blockers: list[str] = []
    caveats: list[str] = []
    if sc_status == "INSUFFICIENT_DATA":
        blockers.append("insufficient_data")
    if agreement == "disagree":
        blockers.append("agreement_conflict")
    # Record analyst fallback in caveats — it is in evidence but excluded from user copy
    if isinstance(av, dict) and bool(av.get("used_fallback", False)):
        caveats.append("analyst_fallback_recorded")

    posture, deploy_posture, blockers, conviction_band = _enforce_insufficient_data_contract(
        sc_status=sc_status,
        action_posture=posture,
        deploy_action_posture=posture,
        blockers=blockers,
        conviction_band=conviction_band,
    )

    watchlist_reason: Optional[str] = None
    if posture == "WATCH":
        if "insufficient_data" in blockers:
            watchlist_reason = "insufficient_data"
        elif "agreement_conflict" in blockers:
            watchlist_reason = "agreement_conflict"
        else:
            watchlist_reason = "insufficient_signal"

    # Section builders
    why = _build_why(
        ticker=ticker,
        analyst_is_usable=analyst_usable,
        av=av,
        sc_status=sc_status,
        published_dimensions=published_dimensions,
        evidence_deterministic=evidence["deterministic"],
    )
    risk = _build_risk(
        analyst_is_usable=analyst_usable,
        av=av,
        sc_status=sc_status,
    )
    action = _build_action(
        ticker=ticker,
        posture=posture,
        analyst_is_usable=analyst_usable,
        av=av,
        sc_status=sc_status,
        agreement=agreement,
    )
    alt_view = _build_alt_view(
        analyst_is_usable=analyst_usable,
        av=av,
    )

    analyst_score: Optional[float] = None
    if isinstance(av, dict):
        try:
            analyst_score = float(av.get("confidence") or 0.0)
        except (TypeError, ValueError):
            analyst_score = None

    return {
        "schema_version": SCHEMA_VERSION,
        "ticker": ticker,
        "why": why,
        "risk": risk,
        "action": action,
        "alt_view": alt_view,
        "confidence": {
            "conviction_band": conviction_band,
            "agreement": agreement,
            "score": analyst_score,
        },
        "deploy_signals": {
            "conviction_band": conviction_band,
            "risk_band": risk_band,
            "data_quality_band": dq_band,
            "action_posture": deploy_posture,
            "watchlist_reason": watchlist_reason,
            "blockers": blockers,
            "caveats": caveats,
        },
        "evidence": evidence,
        "data_quality": data_quality,
    }
