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
import re
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from .feature_engine import FeatureSet
from .market_snapshot import MarketSnapshot

logger = logging.getLogger(__name__)


# ── Validated verdict ──────────────────────────────────────────────────────


ALLOWED_ACTIONS = {"BUY", "HOLD", "REDUCE", "INSUFFICIENT_DATA"}
INSUFFICIENT_DATA_VERDICT_MARKER = "INSUFFICIENT_DATA"
ANALYST_GENERATION_VERSION = "compact_v1"


CONVICTION_LEVELS = {"HIGH", "MEDIUM", "LOW"}


@dataclass
class AnalystVerdict:
    """Strictly-validated per-ticker analyst output.

    * ``action`` ∈ :data:`ALLOWED_ACTIONS`.
    * ``conviction`` ∈ [0.0, 1.0]. Zero when ``action == INSUFFICIENT_DATA``.
    * ``key_drivers`` — max 3 short bullets; ``risks`` — max 2.
    * ``confidence`` ∈ [0.0, 1.0] — self-reported analyst confidence.
    * ``conviction_level`` — HIGH | MEDIUM | LOW categorical label.
    * ``primary_driver`` — single most important plain-English reason.
    * ``risk_flag`` — biggest single risk that could break the thesis.
    * ``action_reason`` — plain-English explanation of why BUY/HOLD/TRIM.
    * ``used_fallback`` — True when the LLM response was unrecoverable and
      we synthesised an ``INSUFFICIENT_DATA`` verdict instead of raising.
    """

    ticker: str
    action: str = INSUFFICIENT_DATA_VERDICT_MARKER
    conviction: float = 0.0
    key_drivers: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    confidence: float = 0.0
    summary: str = ""
    thesis: str = ""
    reasoning: str = ""
    sentiment: Optional[str] = None
    citations: list[str] = field(default_factory=list)
    # Hedge-fund style memo fields
    conviction_level: str = "LOW"
    primary_driver: str = ""
    risk_flag: str = ""
    action_reason: str = ""
    # human_v2 canonical fields (aliases clarify intent for consumers)
    why_this_matters: str = ""      # alias for primary_driver
    what_could_go_wrong: str = ""   # alias for risk_flag
    what_to_do_now: str = ""        # alias for action_reason
    differentiation: str = ""       # why THIS ticker vs similar alternatives
    used_fallback: bool = False
    llm_attempted: bool = False
    analysis_source: str = "live_llm"
    generation_version: str = ANALYST_GENERATION_VERSION
    raw_response: Optional[dict[str, Any]] = None
    parse_diagnostics: Optional[dict[str, Any]] = None
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        # Scrub the raw response out of the default dict — callers that
        # want it should ask explicitly. Keeps log payloads + DB blobs
        # from accidentally carrying transient upstream data.
        out.pop("raw_response", None)
        out.pop("parse_diagnostics", None)
        return out


# ── System prompt ──────────────────────────────────────────────────────────


ANALYST_SYSTEM_PROMPT = """You are a senior portfolio analyst. Write HIGH-SIGNAL, LOW-TOKEN reasoning for ONE ticker.

HARD RULES — auto-rejected if violated:
1. HARD-BANNED (always rejected): SMA, RSI, MACD, moving average, Bollinger, support level, resistance level, price above, uptrend, downtrend.
2. SOFT words allowed ONLY in business context: "revenue trend" OK, "price trend" NOT OK; "demand momentum" OK, "momentum is positive" NOT OK.
3. Use behavior-based language: who is buying/selling and why, what structural edge exists, what named catalyst drives the view.
4. Each field must make a DIFFERENT point — no repeated ideas. No semicolons.
5. Each field ≤ 14 words. One idea per field.
6. reasoning_schema_version must be "compact_v1".
7. If data_quality_score < 0.4: action=INSUFFICIENT_DATA, conviction=0.0, use fallback phrases below.

INSUFFICIENT DATA fallback phrases (use verbatim when data is thin):
  why: "No clear edge vs alternatives."
  risk: "Limited data reduces conviction."
  do: "Hold — no allocation until signal improves."
  alt_view: "—"

Return ONLY valid JSON:
{
  "action": "BUY" | "HOLD" | "REDUCE" | "INSUFFICIENT_DATA",
  "conviction": 0.00,
  "conviction_level": "HIGH" | "MEDIUM" | "LOW",
  "why": "core demand driver or structural edge — named catalyst, ≤14 words",
  "risk": "real-world risk that breaks thesis — business/macro/regulatory, ≤14 words",
  "do": "decision only — Accumulate / Hold / Trim / Buy — no sizing or % numbers, ≤14 words",
  "alt_view": "why this over [named peer or ETF] — e.g. 'vs MSFT: …', ≤14 words",
  "confidence": 0.00,
  "reasoning_schema_version": "compact_v1"
}
"""

ANALYST_STRICT_RETRY_APPENDIX = """
RETRY MODE — previous response rejected. Fix ALL of:
  - Remove hard-banned: SMA / RSI / MACD / moving average / Bollinger / support level / price above / uptrend / downtrend
  - Each field ≤ 14 words, no semicolons, one idea per field
  - do = decision only (Accumulate / Hold / Trim / Buy), no sizing or % allocations
  - alt_view must name a specific peer or ETF comparison
  - why / risk / do / alt_view must each make a different point
  - reasoning_schema_version must be "compact_v1"
Return ONLY valid JSON with keys: action, conviction, conviction_level, why, risk, do, alt_view, confidence, reasoning_schema_version
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
            "trend_direction": feature_set.trend_regime,
            "momentum_score": feature_set.momentum_score,
            "volatility_regime": feature_set.volatility_regime,
            "vs_benchmark_30d": feature_set.relative_strength_30d,
            "vs_benchmark_label": feature_set.relative_strength_label,
            "benchmark_symbol": feature_set.benchmark_symbol,
        },
    }


# ── Validation ─────────────────────────────────────────────────────────────


def validate_verdict(raw: Any, *, ticker: str) -> Optional[AnalystVerdict]:
    """Return an :class:`AnalystVerdict` when ``raw`` matches the schema.

    Returns ``None`` when the response is malformed or fails the repetition
    check — the caller then retries once, then falls back to INSUFFICIENT_DATA.
    """
    if not isinstance(raw, dict):
        return None

    action = str(
        raw.get("action")
        or raw.get("suggested_action")
        or raw.get("recommendation")
        or raw.get("recommendation_action")
        or ""
    ).strip().upper()
    if action not in ALLOWED_ACTIONS:
        return None

    try:
        conviction = float(raw.get("conviction", raw.get("confidence", 0.0)))
        confidence = float(raw.get("confidence", raw.get("conviction", 0.0)))
    except (TypeError, ValueError):
        return None
    if conviction != conviction or confidence != confidence:  # NaN guards
        return None

    conviction = max(0.0, min(1.0, conviction))
    confidence = max(0.0, min(1.0, confidence))

    key_drivers = _coerce_string_list(
        raw.get("drivers") or raw.get("key_drivers") or raw.get("catalysts"),
        max_items=3,
    )
    risks = _coerce_string_list(
        raw.get("risks") or raw.get("main_risks"),
        max_items=2,
    )
    summary = _coerce_short_text(raw.get("summary") or raw.get("short_summary"), max_len=200)
    thesis = _coerce_short_text(raw.get("thesis") or raw.get("investment_thesis"), max_len=300)
    reasoning = _coerce_short_text(
        raw.get("plain_language_explanation")
        or raw.get("reasoning")
        or raw.get("reasoning_summary")
        or raw.get("explanation"),
        max_len=300,
    )
    sentiment = _coerce_short_text(raw.get("sentiment"), max_len=60) or None
    citations = _coerce_string_list(raw.get("citations"), max_items=4)

    # compact_v1 fields (why/risk/do/alt_view) map to the canonical memo fields.
    # Also accept legacy human_v2 field names for backward compatibility.
    primary_driver = _coerce_short_text(
        raw.get("why") or raw.get("primary_driver"), max_len=200
    )
    risk_flag = _coerce_short_text(
        raw.get("risk") or raw.get("risk_flag"), max_len=200
    )
    action_reason = _coerce_short_text(
        raw.get("do") or raw.get("action_reason"), max_len=200
    )
    differentiation = _coerce_short_text(
        raw.get("alt_view") or raw.get("differentiation"), max_len=200
    )
    raw_level = str(raw.get("conviction_level") or "").strip().upper()
    conviction_level = raw_level if raw_level in CONVICTION_LEVELS else _conviction_level_from_score(conviction)

    if action == INSUFFICIENT_DATA_VERDICT_MARKER:
        conviction = 0.0
        conviction_level = "LOW"  # always LOW when there's no data

    # Reject if the memo fields repeat content across each other.
    if _has_field_repetition(primary_driver, risk_flag, action_reason):
        logger.warning(
            "analyst_verdict rejected — field repetition detected ticker=%s", ticker
        )
        return None

    return AnalystVerdict(
        ticker=ticker,
        action=action,
        conviction=conviction,
        key_drivers=key_drivers,
        risks=risks,
        confidence=confidence,
        summary=summary,
        thesis=thesis,
        reasoning=reasoning,
        sentiment=sentiment,
        citations=citations,
        conviction_level=conviction_level,
        primary_driver=primary_driver,
        risk_flag=risk_flag,
        action_reason=action_reason,
        differentiation=differentiation,
        # canonical aliases
        why_this_matters=primary_driver,
        what_could_go_wrong=risk_flag,
        what_to_do_now=action_reason,
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
        conviction_level="LOW",
        primary_driver="No clear edge vs alternatives.",
        risk_flag="Limited data reduces conviction.",
        action_reason="Hold — no allocation until signal improves.",
        differentiation="—",
        why_this_matters="No clear edge vs alternatives.",
        what_could_go_wrong="Limited data reduces conviction.",
        what_to_do_now="Hold — no allocation until signal improves.",
        used_fallback=True,
        llm_attempted=False,
        analysis_source="deterministic_fallback",
        generation_version=ANALYST_GENERATION_VERSION,
        error=error,
    )


def _looks_generic_template(verdict: AnalystVerdict) -> bool:
    """Heuristic guardrail for pseudo-analysis that is mostly templated."""
    text = " ".join([
        verdict.summary, verdict.thesis, verdict.reasoning,
        verdict.primary_driver, verdict.risk_flag, verdict.action_reason,
        verdict.differentiation,
    ]).lower()
    if not text:
        return True
    # Hard-banned phrases always trigger rejection.
    hard_banned = (
        re.compile(r"\babove moving averages?\b"),
        re.compile(r"\bmomentum is (?:positive|negative|strong|weak)\b"),
        re.compile(r"\bsma ?20\b"),
        re.compile(r"\bsma ?50\b"),
        re.compile(r"\brsi\b"),
        re.compile(r"\bmacd\b"),
        re.compile(r"\buptrend\b"),
        re.compile(r"\bdowntrend\b"),
        re.compile(r"\bbullish technicals\b"),
        re.compile(r"\bbearish technicals\b"),
        re.compile(r"\btechnical setup\b"),
        re.compile(r"\bbreaks below (?:a )?moving average\b"),
        re.compile(r"\babove the moving average\b"),
        re.compile(r"\bprice momentum\b"),
        re.compile(r"\bsupport level\b"),
        re.compile(r"\bresistance level\b"),
    )
    for pattern in hard_banned:
        if pattern.search(text):
            return True
    # Two or more template-like data field echoes → reject when no real thesis
    soft_banned = (
        "30d return",
        "trend regime",
        "relative strength",
        "watchlist-style view",
        "strong fundamentals support",
        "positive outlook",
        "constructive setup",
        "price weakness",
        "favorable conditions",
        "good setup",
    )
    marker_hits = sum(1 for m in soft_banned if m in text)
    has_thesis_shape = ("because" in text) or ("risk" in text) or ("invalidate" in text)
    return marker_hits >= 2 and not has_thesis_shape


_BANNED_INDICATOR_PATTERNS = (
    # Hard-banned indicator names
    re.compile(r"\bmoving averages?\b", re.IGNORECASE),
    re.compile(r"\bsma\d*\b", re.IGNORECASE),
    re.compile(r"\brsi\b", re.IGNORECASE),
    re.compile(r"\bmacd\b", re.IGNORECASE),
    re.compile(r"\bbollinger\b", re.IGNORECASE),
    re.compile(r"\bsupport level\b", re.IGNORECASE),
    re.compile(r"\bresistance level\b", re.IGNORECASE),
    re.compile(r"\bprice above\b", re.IGNORECASE),
    re.compile(r"\boutperforming (?:the )?broad market\b", re.IGNORECASE),
    # Technical-context-specific trend/momentum (not business language)
    re.compile(r"\buptrend\b", re.IGNORECASE),
    re.compile(r"\bdowntrend\b", re.IGNORECASE),
    re.compile(r"\bprice momentum\b", re.IGNORECASE),
    re.compile(r"\bmomentum is (?:positive|negative|strong|weak)\b", re.IGNORECASE),
    re.compile(r"\btechnical(?:ly)? (?:in a )?trend\b", re.IGNORECASE),
)


def _contains_banned_indicator_language(verdict: AnalystVerdict) -> bool:
    text = " ".join(
        [
            verdict.summary,
            verdict.thesis,
            verdict.reasoning,
            verdict.primary_driver,
            verdict.risk_flag,
            verdict.action_reason,
            verdict.differentiation,
            *verdict.key_drivers,
            *verdict.risks,
        ]
    ).lower()
    if not text:
        return False
    return any(pattern.search(text) for pattern in _BANNED_INDICATOR_PATTERNS)


def _extract_normalized_sentences(verdict: AnalystVerdict) -> set[str]:
    source = " ".join(
        [
            verdict.summary,
            verdict.thesis,
            verdict.reasoning,
            verdict.primary_driver,
            verdict.risk_flag,
            verdict.action_reason,
        ]
    ).lower()
    chunks = re.split(r"[.!?;\n]+", source)
    out: set[str] = set()
    for raw in chunks:
        cleaned = re.sub(r"\s+", " ", raw).strip(" -,:")
        if len(cleaned) >= 35:
            out.add(cleaned)
    return out


def _find_cross_ticker_similarity_offenders(
    verdicts: dict[str, AnalystVerdict],
) -> set[str]:
    offenders: set[str] = set()
    tickers = [t for t, v in verdicts.items() if not v.used_fallback]
    sentence_map = {ticker: _extract_normalized_sentences(verdicts[ticker]) for ticker in tickers}
    for idx, ticker_a in enumerate(tickers):
        for ticker_b in tickers[idx + 1 :]:
            shared = sentence_map[ticker_a] & sentence_map[ticker_b]
            if shared:
                offenders.add(ticker_a)
                offenders.add(ticker_b)
    return offenders


# ── Single-ticker analyst call ─────────────────────────────────────────────


async def analyze_ticker(
    *,
    snapshot: MarketSnapshot,
    feature_set: FeatureSet,
    llm,  # Duck-typed LLMClient with ``ask_json``
    semaphore: Optional[asyncio.Semaphore] = None,
    max_tokens: int = 250,
    strict_mode_only: bool = False,
) -> AnalystVerdict:
    """Run one analyst LLM call with schema validation + one retry.

    Never raises. Returns an ``INSUFFICIENT_DATA`` verdict when both
    attempts yield unparseable or invalid JSON.
    """
    # Data-quality gate: bypass the LLM only when evidence is both very thin
    # and structurally sparse. We avoid over-skipping when upstream price/news
    # providers are partially degraded but there is still enough context for a
    # useful thesis.
    has_price = snapshot.price is not None and float(snapshot.price or 0) > 0
    has_return_signal = any(
        v is not None for v in (snapshot.return_1d, snapshot.return_5d, snapshot.return_30d)
    )
    has_news = bool((snapshot.recent_headlines or [])[:1]) or int(snapshot.news_count or 0) > 0
    has_fundamentals = isinstance(snapshot.fundamentals, dict) and bool(snapshot.fundamentals)
    evidence_signals = sum(1 for flag in (has_price, has_return_signal, has_news, has_fundamentals) if flag)
    quality_skip = snapshot.data_quality_score < 0.25 and evidence_signals < 2
    if quality_skip:
        logger.info(
            "llm_skipped_reason stage=per_ticker ticker=%s reason=data_quality_below_threshold "
            "quality=%.2f evidence_signals=%d",
            snapshot.ticker,
            snapshot.data_quality_score,
            evidence_signals,
        )
        logger.info(
            "fallback_trigger_reason stage=per_ticker ticker=%s reason=data_quality_below_threshold",
            snapshot.ticker,
        )
        return insufficient_data_verdict(
            snapshot.ticker, error="data_quality_below_threshold",
        )

    payload = build_analyst_inputs(snapshot=snapshot, feature_set=feature_set)
    payload["_reasoning_nonce"] = str(uuid.uuid4())
    user_msg = json.dumps(payload, default=str)
    logger.info(
        "analyst_stage.prompt_built ticker=%s payload=%s",
        snapshot.ticker,
        user_msg[:1500],
    )

    async def _call_once(*, system_prompt: str, token_budget: int, call_meta: dict[str, Any]) -> Any:
        async def _invoke(with_metadata: bool) -> Any:
            kwargs = {
                "system": system_prompt,
                "user": user_msg,
                "max_tokens": token_budget,
            }
            if with_metadata:
                kwargs["metadata"] = call_meta
            return await llm.ask_json(**kwargs)

        if semaphore is not None:
            async with semaphore:
                try:
                    return await _invoke(with_metadata=True)
                except TypeError:
                    return await _invoke(with_metadata=False)
        try:
            return await _invoke(with_metadata=True)
        except TypeError:
            return await _invoke(with_metadata=False)

    retry_reason: str | None = None
    strict_mode = False
    truncation_retry_used = False
    last_meta: dict[str, Any] = {}
    attempts = (1,) if strict_mode_only else (1, 2)
    for attempt in attempts:
        call_meta: dict[str, Any] = {}
        if strict_mode_only:
            strict_mode = True
        token_budget = max_tokens + (180 if strict_mode else 0)
        system_prompt = (
            f"{ANALYST_SYSTEM_PROMPT}\n\n{ANALYST_STRICT_RETRY_APPENDIX}"
            if strict_mode
            else ANALYST_SYSTEM_PROMPT
        )
        try:
            logger.info("llm_call_started stage=per_ticker ticker=%s attempt=%d", snapshot.ticker, attempt)
            raw = await _call_once(
                system_prompt=system_prompt,
                token_budget=token_budget,
                call_meta=call_meta,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "llm_call_failed stage=per_ticker ticker=%s attempt=%d err=%s",
                snapshot.ticker,
                attempt,
                exc,
            )
            raw = {}
        last_meta = call_meta
        logger.info(
            "analyst_stage.parse_observability ticker=%s attempt=%d raw_response_length=%s "
            "had_code_fence=%s extracted_json_length=%s parse_error_type=%s "
            "truncated_response_detected=%s retry_reason=%s",
            snapshot.ticker,
            attempt,
            call_meta.get("primary_raw_response_length")
            or call_meta.get("fallback_raw_response_length")
            or 0,
            bool(call_meta.get("primary_had_code_fence") or call_meta.get("fallback_had_code_fence")),
            call_meta.get("primary_extracted_json_length")
            or call_meta.get("fallback_extracted_json_length")
            or 0,
            call_meta.get("primary_parse_error_type")
            or call_meta.get("fallback_parse_error_type")
            or "none",
            bool(
                call_meta.get("primary_truncated_response_detected")
                or call_meta.get("fallback_truncated_response_detected")
            ),
            retry_reason or "",
        )

        logger.info(
            "analyst_trace checkpoint=raw_response ticker=%s attempt=%d raw=%s",
            snapshot.ticker,
            attempt,
            json.dumps(raw, default=str)[:1500],
        )
        verdict = validate_verdict(raw, ticker=snapshot.ticker)
        if verdict is None:
            logger.warning(
                "llm_response_empty stage=per_ticker ticker=%s attempt=%d reason=schema_validation_failed",
                snapshot.ticker,
                attempt,
            )
            retry_reason = (
                call_meta.get("primary_parse_error_type")
                or call_meta.get("fallback_parse_error_type")
                or "schema_validation_failed"
            )
            strict_mode = True
            if retry_reason == "truncated_json":
                truncation_retry_used = True
            continue
        verdict.llm_attempted = True
        verdict.analysis_source = "live_llm"
        verdict.parse_diagnostics = call_meta
        if _contains_banned_indicator_language(verdict):
            retry_reason = "banned_indicator_language"
            logger.warning(
                "analyst_stage.quality_guard_reject ticker=%s attempt=%d reason=%s",
                snapshot.ticker,
                attempt,
                retry_reason,
            )
            strict_mode = True
            continue
        if _looks_generic_template(verdict):
            retry_reason = "generic_template_rejected"
            logger.warning(
                "analyst_stage.quality_guard_reject ticker=%s attempt=%d reason=%s",
                snapshot.ticker,
                attempt,
                retry_reason,
            )
            strict_mode = True
            continue
        logger.info(
            "llm_call_completed stage=per_ticker ticker=%s attempt=%d normalized_success=%s verdict=%s",
            snapshot.ticker,
            attempt,
            True,
            json.dumps(verdict.to_dict(), default=str)[:1500],
        )
        logger.debug(
            "reasoning_contract_trace normalized_keys=%s ticker=%s",
            sorted(verdict.to_dict().keys()),
            snapshot.ticker,
        )
        return verdict

    logger.warning(
        "fallback_trigger_reason stage=per_ticker ticker=%s reason=%s",
        snapshot.ticker,
        retry_reason or "schema_validation_failed",
    )
    fallback = insufficient_data_verdict(
        snapshot.ticker, error=retry_reason or "schema_validation_failed",
    )
    fallback.llm_attempted = True
    fallback.parse_diagnostics = {
        **last_meta,
        "normalized_success": False,
        "retry_reason": retry_reason,
        "truncation_retry_used": truncation_retry_used,
        "fallback_reason": retry_reason or "schema_validation_failed",
    }
    return fallback


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
        logger.info("LLM reasoning regenerated for ticker %s", _t)

    similarity_offenders = _find_cross_ticker_similarity_offenders(out)
    if similarity_offenders:
        logger.warning(
            "analyst_stage.cross_ticker_similarity_reject tickers=%s",
            sorted(similarity_offenders),
        )
        for ticker in sorted(similarity_offenders):
            snap = snapshots.get(ticker)
            fs = features.get(ticker)
            if snap is None or fs is None:
                out[ticker] = insufficient_data_verdict(ticker, error="missing_inputs_for_similarity_retry")
                continue
            out[ticker] = await analyze_ticker(
                snapshot=snap,
                feature_set=fs,
                llm=llm,
                semaphore=semaphore,
                strict_mode_only=True,
            )
            logger.info("LLM reasoning regenerated for ticker %s", ticker)

        post_retry_offenders = _find_cross_ticker_similarity_offenders(out)
        for ticker in post_retry_offenders:
            out[ticker] = insufficient_data_verdict(
                ticker,
                error="cross_ticker_similarity_rejected",
            )
    total_llm_requests = len(out)
    parse_successes = sum(1 for v in out.values() if not v.used_fallback)
    fenced_json_rescued = sum(
        1 for v in out.values()
        if isinstance(v.parse_diagnostics, dict)
        and (
            v.parse_diagnostics.get("primary_had_code_fence")
            or v.parse_diagnostics.get("fallback_had_code_fence")
        )
        and not v.used_fallback
    )
    truncation_retries = sum(
        1 for v in out.values()
        if isinstance(v.parse_diagnostics, dict)
        and (
            v.parse_diagnostics.get("retry_reason") == "truncated_response_detected"
            or v.parse_diagnostics.get("truncation_retry_used")
        )
    )
    schema_normalized_successes = sum(
        1 for v in out.values()
        if not v.used_fallback and (v.reasoning or v.thesis or v.summary)
    )
    true_fallbacks = sum(1 for v in out.values() if v.used_fallback)
    logger.info(
        "analyst_stage.parse_summary total_llm_requests=%d parse_successes=%d "
        "fenced_json_rescued=%d truncation_retries=%d schema_normalized_successes=%d "
        "true_fallbacks=%d",
        total_llm_requests,
        parse_successes,
        fenced_json_rescued,
        truncation_retries,
        schema_normalized_successes,
        true_fallbacks,
    )
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
    """Render a compact 4-line thesis block from the memo fields.

    Compact format: one labeled line each for WHY / RISK / ACTION / ALT VIEW.
    Falls back to a minimal single-line when memo fields are missing.
    """
    if verdict.primary_driver:
        parts: list[str] = [f"WHY: {verdict.primary_driver.rstrip('.')}"]
        if verdict.risk_flag:
            parts.append(f"RISK: {verdict.risk_flag.rstrip('.')}")
        if verdict.action_reason:
            parts.append(f"ACTION: {verdict.action_reason.rstrip('.')}")
        alt = verdict.differentiation or "—"
        parts.append(f"ALT VIEW: {alt.rstrip('.')}")
        return "\n".join(parts)[:400]

    preferred = verdict.reasoning.strip() or verdict.thesis.strip() or verdict.summary.strip()
    if preferred:
        return preferred[:300]

    fallback_by_action = {
        "BUY": "Conviction-backed buy.",
        "REDUCE": "Risk elevated vs reward.",
        "HOLD": "Mixed signal — hold.",
        "INSUFFICIENT_DATA": "No clear edge — hold.",
    }
    return fallback_by_action.get(verdict.action, "Hold.")


# ── Small helpers ──────────────────────────────────────────────────────────


def _conviction_level_from_score(conviction: float) -> str:
    """Derive conviction_level categorical label from numeric conviction."""
    if conviction >= 0.65:
        return "HIGH"
    if conviction >= 0.35:
        return "MEDIUM"
    return "LOW"


def _has_field_repetition(primary_driver: str, risk_flag: str, action_reason: str) -> bool:
    """Return True when the same substantive sentence appears in 2+ memo fields.

    Splits each field into sentences, normalises whitespace, and checks for
    exact-sentence duplicates across the three fields. Returns False (no
    rejection) when any field is empty — the retry prompt handles missing
    fields separately.
    """
    if not (primary_driver and risk_flag and action_reason):
        return False

    def _sentences(text: str) -> set[str]:
        return {
            s.strip().lower().rstrip(".")
            for s in text.replace("!", ".").replace("?", ".").split(".")
            if len(s.strip()) > 20  # ignore very short fragments
        }

    pd_sents = _sentences(primary_driver)
    rf_sents = _sentences(risk_flag)
    ar_sents = _sentences(action_reason)

    return bool(
        pd_sents & rf_sents
        or pd_sents & ar_sents
        or rf_sents & ar_sents
    )


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


def _coerce_short_text(v: Any, *, max_len: int) -> str:
    if isinstance(v, str):
        return v.strip()[:max_len]
    return ""
