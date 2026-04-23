"""Phase 3 — per-ticker LLM analyst.

Replaces the monolithic portfolio-agent call with one analyst call per
ticker. Inputs are the Phase 1 :class:`MarketSnapshot` + Phase 2
:class:`FeatureSet`; output is a strictly-validated
:class:`AnalystVerdict` used by both the Phase 4 synthesis stage and
the ``agent_insights`` persistence layer.

Contract invariants:
  * The LLM interprets structured inputs only — it does NOT recompute
    indicators. The system prompt is explicit about this.
  * Strict schema validation on every response. One retry on malformed
    JSON or constraint violations; second failure → ``INSUFFICIENT_DATA``
    verdict (NEVER an empty dict).
  * Concurrent analyst calls are capped by a per-orchestrator semaphore
    so a 34-ticker portfolio doesn't stampede the Anthropic API.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from .feature_engine import FeatureSet
from .market_snapshot import MarketSnapshot

logger = logging.getLogger(__name__)


# ── Validated verdict ──────────────────────────────────────────────────────


ALLOWED_ACTIONS = {"BUY", "HOLD", "REDUCE", "INSUFFICIENT_DATA"}
INSUFFICIENT_DATA_VERDICT_MARKER = "INSUFFICIENT_DATA"


@dataclass
class AnalystVerdict:
    """Strictly-validated per-ticker analyst output.

    * ``action`` ∈ :data:`ALLOWED_ACTIONS`.
    * ``conviction`` ∈ [0.0, 1.0]. Zero when ``action == INSUFFICIENT_DATA``.
    * ``key_drivers`` — max 3 short bullets; ``risks`` — max 2.
    * ``confidence`` ∈ [0.0, 1.0] — self-reported analyst confidence.
    * ``used_fallback`` — True when the LLM response was unrecoverable and
      we synthesised an ``INSUFFICIENT_DATA`` verdict instead of raising.
    """

    ticker: str
    action: str = INSUFFICIENT_DATA_VERDICT_MARKER
    conviction: float = 0.0
    key_drivers: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    confidence: float = 0.0
    used_fallback: bool = False
    raw_response: Optional[dict[str, Any]] = None
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        # Scrub the raw response out of the default dict — callers that
        # want it should ask explicitly. Keeps log payloads + DB blobs
        # from accidentally carrying transient upstream data.
        out.pop("raw_response", None)
        return out


# ── System prompt ──────────────────────────────────────────────────────────


ANALYST_SYSTEM_PROMPT = """You are a disciplined per-ticker equity analyst.

INPUTS — you receive a JSON object with these keys:
  - "ticker": string.
  - "snapshot": MarketSnapshot with price / returns / volatility /
    sector / sentiment / fundamentals / news.
  - "features": FeatureSet with trend_regime / momentum_score /
    volatility_regime / relative_strength_label.

RULES (hard requirements):
  1. You interpret the structured inputs. You NEVER recompute indicators,
     invent headlines, estimate P/E, or infer missing numbers.
  2. When a field is missing you say so in the reasoning — you do NOT
     guess.
  3. When snapshot.data_quality_score < 0.4 default the action to
     INSUFFICIENT_DATA and conviction to 0.0. Do not force a directional
     call on thin data.
  4. key_drivers list: MAX 3 short phrases grounded in the inputs.
     risks list: MAX 2 short phrases.
  5. action MUST be one of: "BUY", "HOLD", "REDUCE", "INSUFFICIENT_DATA".
     REDUCE means trim the existing position.
  6. conviction ∈ [0.0, 1.0], confidence ∈ [0.0, 1.0]. Both zero when
     you return INSUFFICIENT_DATA.

OUTPUT — return ONLY this JSON, no preamble, no code fences:
{
  "action": "BUY" | "HOLD" | "REDUCE" | "INSUFFICIENT_DATA",
  "conviction": 0.00,
  "key_drivers": ["driver 1", "driver 2"],
  "risks": ["risk 1"],
  "confidence": 0.00
}
"""


# ── Pure-ish helpers ───────────────────────────────────────────────────────


def build_analyst_inputs(
    *,
    snapshot: MarketSnapshot,
    feature_set: FeatureSet,
) -> dict[str, Any]:
    """Compact JSON payload fed to the analyst LLM.

    The Phase 1 snapshot + Phase 2 features already carry every field we
    want the LLM to reason over — we only need to drop bytes-heavy slices
    (full headlines list, raw fundamentals) and keep the structured signal.
    """
    return {
        "ticker": snapshot.ticker,
        "snapshot": {
            "price": snapshot.price,
            "price_source": snapshot.price_source,
            "return_1d": snapshot.return_1d,
            "return_5d": snapshot.return_5d,
            "return_30d": snapshot.return_30d,
            "volatility_30d": snapshot.volatility_30d,
            "sector": snapshot.sector,
            "industry": snapshot.industry,
            "category": snapshot.category,
            "sentiment_label": snapshot.sentiment_label,
            "sentiment_score": snapshot.sentiment_score,
            "news_count": snapshot.news_count,
            "recent_headlines": snapshot.recent_headlines[:3],
            "fundamentals": snapshot.fundamentals,
            "data_quality_score": snapshot.data_quality_score,
            "missing_fields": snapshot.missing_fields,
        },
        "features": {
            "trend_regime": feature_set.trend_regime,
            "momentum_score": feature_set.momentum_score,
            "volatility_regime": feature_set.volatility_regime,
            "relative_strength_30d": feature_set.relative_strength_30d,
            "relative_strength_label": feature_set.relative_strength_label,
            "benchmark_symbol": feature_set.benchmark_symbol,
            "sma20": feature_set.sma20,
            "sma50": feature_set.sma50,
        },
    }


# ── Validation ─────────────────────────────────────────────────────────────


def validate_verdict(raw: Any, *, ticker: str) -> Optional[AnalystVerdict]:
    """Return an :class:`AnalystVerdict` when ``raw`` matches the schema.

    Returns ``None`` when the response is malformed — the caller then
    retries once and falls back to ``INSUFFICIENT_DATA`` on second failure.
    """
    if not isinstance(raw, dict):
        return None

    action = str(raw.get("action") or "").strip().upper()
    if action not in ALLOWED_ACTIONS:
        return None

    try:
        conviction = float(raw.get("conviction", 0.0))
        confidence = float(raw.get("confidence", 0.0))
    except (TypeError, ValueError):
        return None
    if conviction != conviction or confidence != confidence:  # NaN guards
        return None

    conviction = max(0.0, min(1.0, conviction))
    confidence = max(0.0, min(1.0, confidence))

    key_drivers = _coerce_string_list(raw.get("key_drivers"), max_items=3)
    risks = _coerce_string_list(raw.get("risks"), max_items=2)

    if action == INSUFFICIENT_DATA_VERDICT_MARKER:
        conviction = 0.0

    return AnalystVerdict(
        ticker=ticker,
        action=action,
        conviction=conviction,
        key_drivers=key_drivers,
        risks=risks,
        confidence=confidence,
        raw_response=raw,
    )


def insufficient_data_verdict(
    ticker: str, *, error: Optional[str] = None,
) -> AnalystVerdict:
    """Deterministic, spec-mandated fallback verdict.

    Used when the LLM response is unrecoverable after one retry, or when
    the data layer signals that reasoning on the ticker is unsafe.
    """
    return AnalystVerdict(
        ticker=ticker,
        action=INSUFFICIENT_DATA_VERDICT_MARKER,
        conviction=0.0,
        key_drivers=[],
        risks=[],
        confidence=0.0,
        used_fallback=True,
        error=error,
    )


# ── Single-ticker analyst call ─────────────────────────────────────────────


async def analyze_ticker(
    *,
    snapshot: MarketSnapshot,
    feature_set: FeatureSet,
    llm,  # Duck-typed LLMClient with ``ask_json``
    semaphore: Optional[asyncio.Semaphore] = None,
    max_tokens: int = 350,
) -> AnalystVerdict:
    """Run one analyst LLM call with schema validation + one retry.

    Never raises. Returns an ``INSUFFICIENT_DATA`` verdict when both
    attempts yield unparseable or invalid JSON.
    """
    # Data-quality gate: bypass the LLM entirely for the thinnest tickers.
    # Saves tokens and guarantees the verdict shape the spec mandates.
    if snapshot.data_quality_score < 0.25:
        logger.info(
            "analyst bypass (quality<0.25) ticker=%s quality=%.2f",
            snapshot.ticker, snapshot.data_quality_score,
        )
        return insufficient_data_verdict(
            snapshot.ticker, error="data_quality_below_threshold",
        )

    payload = build_analyst_inputs(snapshot=snapshot, feature_set=feature_set)
    user_msg = json.dumps(payload, default=str)

    async def _call_once() -> Any:
        if semaphore is not None:
            async with semaphore:
                return await llm.ask_json(
                    system=ANALYST_SYSTEM_PROMPT,
                    user=user_msg,
                    max_tokens=max_tokens,
                )
        return await llm.ask_json(
            system=ANALYST_SYSTEM_PROMPT,
            user=user_msg,
            max_tokens=max_tokens,
        )

    # ── Attempt 1 ──────────────────────────────────────────────────────
    try:
        raw = await _call_once()
    except Exception as exc:  # noqa: BLE001
        logger.warning("analyst attempt 1 raised ticker=%s err=%s",
                       snapshot.ticker, exc)
        raw = {}

    verdict = validate_verdict(raw, ticker=snapshot.ticker)
    if verdict is not None:
        return verdict

    # ── Attempt 2 (retry) ──────────────────────────────────────────────
    logger.info("analyst retry ticker=%s raw=%s", snapshot.ticker,
                str(raw)[:120])
    try:
        raw = await _call_once()
    except Exception as exc:  # noqa: BLE001
        logger.warning("analyst attempt 2 raised ticker=%s err=%s",
                       snapshot.ticker, exc)
        raw = {}

    verdict = validate_verdict(raw, ticker=snapshot.ticker)
    if verdict is not None:
        return verdict

    logger.warning(
        "analyst exhausted retries ticker=%s — returning INSUFFICIENT_DATA",
        snapshot.ticker,
    )
    return insufficient_data_verdict(
        snapshot.ticker, error="schema_validation_failed",
    )


# ── Portfolio-wide parallel analyst ────────────────────────────────────────


async def analyze_portfolio(
    *,
    snapshots: dict[str, MarketSnapshot],
    features: dict[str, FeatureSet],
    llm,
    max_concurrency: int = 3,
) -> dict[str, AnalystVerdict]:
    """Run the per-ticker analyst for every snapshot concurrently.

    ``max_concurrency`` caps the number of in-flight LLM calls so a
    34-ticker portfolio doesn't stampede the API. Every ticker is
    guaranteed to have an entry in the returned map — on unrecoverable
    error the entry is an ``INSUFFICIENT_DATA`` verdict, never missing.
    """
    if not snapshots:
        return {}

    semaphore = asyncio.Semaphore(max(1, max_concurrency))

    async def _run(ticker: str, snap: MarketSnapshot) -> tuple[str, AnalystVerdict]:
        fs = features.get(ticker)
        if fs is None:
            return ticker, insufficient_data_verdict(
                ticker, error="missing_feature_set",
            )
        verdict = await analyze_ticker(
            snapshot=snap,
            feature_set=fs,
            llm=llm,
            semaphore=semaphore,
        )
        return ticker, verdict

    tasks = [
        asyncio.create_task(_run(ticker, snap))
        for ticker, snap in snapshots.items()
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    out: dict[str, AnalystVerdict] = {}
    for ticker, item in zip(snapshots.keys(), results):
        if isinstance(item, BaseException):
            logger.warning("analyst task raised ticker=%s err=%s", ticker, item)
            out[ticker] = insufficient_data_verdict(
                ticker, error=f"task_exception:{item}",
            )
            continue
        _t, verdict = item  # tuple from ``_run``
        out[_t] = verdict
    return out


# ── Persistence mapping ────────────────────────────────────────────────────


def action_to_suggested_action(action: str) -> str:
    """Map analyst action onto the legacy ``suggested_action`` enum.

    The ``agent_insights.suggested_action`` CHECK constraint predates
    Phase 3 and only permits BUY/SELL/TRIM/HOLD/REVIEW. We map
    REDUCE→TRIM and INSUFFICIENT_DATA→HOLD (with a REVIEW flag in the
    thesis) so existing persisted rows + frontend code keep working.
    """
    if action == "BUY":
        return "BUY"
    if action == "REDUCE":
        return "TRIM"
    if action == "INSUFFICIENT_DATA":
        return "HOLD"
    return "HOLD"


def format_thesis(verdict: AnalystVerdict) -> str:
    """Render a plain-English thesis (max ~2 short sentences)."""
    lead_by_action = {
        "BUY": "The setup still looks constructive",
        "REDUCE": "Risk now looks elevated versus reward",
        "HOLD": "The setup looks balanced for now",
        "INSUFFICIENT_DATA": "Evidence is limited right now",
    }
    lead = lead_by_action.get(verdict.action, "The setup is mixed right now")

    sentence_1 = lead
    if verdict.key_drivers:
        sentence_1 += f" because {verdict.key_drivers[0].rstrip('.')}."
    else:
        sentence_1 += "."

    sentence_2 = ""
    if verdict.risks:
        sentence_2 = f"Main watch item: {verdict.risks[0].rstrip('.')}."
    elif verdict.action == "INSUFFICIENT_DATA" or verdict.used_fallback:
        sentence_2 = (
            "This is a watchlist-style view until more external evidence is available."
        )

    return f"{sentence_1} {sentence_2}".strip()[:500]


# ── Small helpers ──────────────────────────────────────────────────────────


def _coerce_string_list(v: Any, *, max_items: int) -> list[str]:
    if not isinstance(v, list):
        return []
    out: list[str] = []
    for item in v:
        if not isinstance(item, str):
            continue
        s = item.strip()
        if s:
            out.append(s[:200])
        if len(out) >= max_items:
            break
    return out
