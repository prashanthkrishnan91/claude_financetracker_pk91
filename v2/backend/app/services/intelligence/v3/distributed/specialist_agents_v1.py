"""Distributed Run Intel — pure financial specialist analyzers.

Specialists consume ONLY the immutable evidence bundle. They never call
providers, never read anything outside the session's durable rows, and never
set visible actions — their outputs are structured advisory research persisted
per (ticker, axis) in ``intel_run_specialist_outputs``.

LLM plumbing reuses the existing Anthropic client (``agents/llm.py``
``LLMClient.ask_json``): no new agent framework, existing model/failover
config. One batched Claude request analyzes 1..N compatible tickers per
(asset_type, axis) task; strict JSON is validated per ticker with one bounded
repair retry; a malformed ticker degrades only itself.

Boundary proof: this module intentionally imports NO provider machinery of
any kind — the architecture-boundary test asserts that.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from ....agents.llm import NON_RETRYABLE_PROVIDER_CLASSES
from . import conflict_policy_v1
from . import evidence_bundle_v1
from . import run_task_store_v1 as store
from . import source_lineage_v1
from .run_scheduler_v1 import parse_batch_tickers
from .task_contracts_v1 import (
    ALLOWED_STANCES,
    AXIS_CRYPTO_MARKET,
    AXIS_ETF_EXPOSURE,
    AXIS_FUNDAMENTAL,
    AXIS_REVIEW,
    AXIS_RISK_FILING,
    AXIS_SENTIMENT,
    AXIS_TECHNICAL,
    LANE_CRYPTO_MARKET,
    LANE_ETF_FUND_DATA,
    LANE_FUNDAMENTALS,
    LANE_NEWS_SENTIMENT,
    LANE_SEC_CATALYST,
    LANE_SEC_COMPANY_FACTS,
    LANE_TECHNICALS,
    TASK_DEGRADED,
    TASK_SUCCEEDED,
    stable_fingerprint,
)
from .run_task_store_v1 import TASK_FAILED_RETRYABLE

logger = logging.getLogger(__name__)

# v2: the prompt contract now carries a compact, bounded source projection
# (contract §3) — a reused output from the v1 (unsourced) prompt contract
# must never be treated as equivalent and is never reused (contract §4/§13).
PROMPT_VERSION = "distributed_specialist_v3"  # v3: currency-labeled monetary evidence + filtered news
# How long a specialist output stays reusable for an unchanged evidence
# fingerprint (skips duplicate LLM calls across sessions).
OUTPUT_VALID_HOURS = 24.0

# Bounded per-call Haiku output budget for compact JSON — scales with the
# ticker count actually IN this call (batch or single-ticker repair), never
# unbounded. Examples: 1 ticker -> 700, 2 tickers -> 1300, 3+ tickers capped
# at 1800.
SPECIALIST_TOKENS_PER_TICKER = 650
SPECIALIST_MIN_TOKENS_PER_CALL = 700
SPECIALIST_MAX_TOKENS_PER_CALL = 1800

# Per-result field caps enforced by validate_specialist_result — mirrors the
# compact-JSON limits stated in SPECIALIST_SYSTEM_PROMPT.
_MAX_LIST_ITEMS = 2
_MAX_STRING_CHARS = 120


def _specialist_token_budget(ticker_count: int) -> int:
    n = max(1, ticker_count)
    return max(
        SPECIALIST_MIN_TOKENS_PER_CALL,
        min(SPECIALIST_MAX_TOKENS_PER_CALL, SPECIALIST_TOKENS_PER_TICKER * n),
    )

_AXIS_FOCUS: dict[str, str] = {
    AXIS_FUNDAMENTAL: (
        "fundamental quality and valuation: profitability, growth, balance "
        "sheet, cash generation, and whether the valuation multiples in the "
        "evidence look demanding or undemanding relative to those fundamentals"
    ),
    AXIS_TECHNICAL: (
        "technical and market-regime posture: trend from the provided moving "
        "averages and returns, momentum, volatility regime, and drawdown "
        "context — using ONLY the numbers in the evidence"
    ),
    AXIS_SENTIMENT: (
        "news and event sentiment and catalysts: the tone and materiality of "
        "the provided headlines/events and any upcoming catalyst risk"
    ),
    AXIS_RISK_FILING: (
        "filing, governance and downside risk: what the provided SEC-derived "
        "evidence implies about leverage, dilution, disclosure changes and "
        "tail risks"
    ),
    AXIS_ETF_EXPOSURE: (
        "ETF exposure and role: what the fund holds/represents based on the "
        "provided profile/holdings evidence, concentration, cost context, and "
        "how it behaves as a portfolio building block"
    ),
    AXIS_CRYPTO_MARKET: (
        "crypto market posture: momentum, volatility, drawdown from ATH, "
        "market-cap rank, liquidity context and community sentiment votes in "
        "the provided data"
    ),
}

SPECIALIST_SYSTEM_PROMPT = """You are a specialist financial research analyst.
You receive normalized evidence bundles for one or more tickers. Analyze ONLY
the evidence provided — never invent numbers, never assume data you were not
given, never browse. If evidence for a field is missing, list it in
missing_evidence and lower your confidence instead of guessing.

You produce ADVISORY research only. You do NOT make buy/hold/trim/sell
decisions — a deterministic policy engine owns those. Never output a
Buy/Hold/Trim/Sell action word anywhere in your response.

Output COMPACT JSON ONLY. Exceeding any limit below is INVALID output:
- No markdown, no code fences, no commentary before or after the JSON.
- Exactly one result object per requested ticker, using the exact requested
  ticker symbol — never abbreviate, expand, or invent a symbol.
- key_findings: 1-2 items — never empty, even for thin evidence (state the
  strongest evidence-grounded observation, however limited).
- risks: at most 2 items.
- missing_evidence: at most 2 items.
- limitations: at most 2 items.
- Every string in every list: at most ~120 characters — a short,
  evidence-based phrase, never a paragraph.

Return exactly this JSON shape:
{"results": [{
  "ticker": "...",
  "stance": "positive" | "neutral" | "negative",
  "score": <float -1.0..1.0>,
  "confidence": <float 0.0..1.0>,
  "key_findings": ["...", ...],
  "risks": ["...", ...],
  "missing_evidence": ["...", ...],
  "limitations": ["...", ...]
}]}
Every requested ticker MUST appear exactly once, in any order."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


_FUNDAMENTAL_RATIO_KEYS = (
    "pe", "forward_pe", "peg", "ps_ttm", "ev_ebitda", "profit_margin",
    "gross_margin", "revenue_growth", "earnings_growth", "debt_to_equity",
    "return_on_equity", "beta", "dividend_yield", "sector", "industry",
)
_RISK_RATIO_KEYS = ("debt_to_equity", "beta")
_RISK_MONETARY_KEYS = ("total_debt", "cash")


def _compact_fundamental(
    fundamental: Optional[dict[str, Any]], *,
    ratio_keys: tuple = _FUNDAMENTAL_RATIO_KEYS,
    monetary_keys: Optional[tuple] = None,
) -> dict[str, Any]:
    """Currency-safe fundamentals for the LLM prompt: ratios verbatim
    (dimensionless), monetary fields ONLY as their verified-currency compact
    projection ("USD 12.4 billion") — never the raw ambiguous number, and
    never guessed when the reporting currency is unknown."""
    fundamental = fundamental or {}
    normalized = fundamental.get("normalized") or {}
    compact = normalized.get("compact") or {}
    gaps = normalized.get("normalization_gaps") or []
    if monetary_keys is not None:
        compact = {k: v for k, v in compact.items() if k in monetary_keys}
        gaps = [g for g in gaps if g in monetary_keys]
    out = {k: fundamental.get(k) for k in ratio_keys if fundamental.get(k) not in (None, "")}
    if compact:
        out["monetary"] = compact
    if normalized.get("reporting_currency"):
        out["reporting_currency"] = normalized["reporting_currency"]
    if gaps:
        out["normalization_gaps"] = gaps
    return out


def _compact_bundle_for_axis(bundle: dict[str, Any], axis: str) -> dict[str, Any]:
    """Trim the bundle to what the axis needs (prompt-size control).

    Never includes ``market`` (volatile intraday tick — the ``technical``
    lane's daily history already carries the price signal specialists reason
    over) or ``prior_action`` (an immediate rerun's own just-published
    decision). Both are excluded from the prompt AND the fingerprint
    (``axis_evidence_context``) together — a field must never be visible to
    the LLM while invisible to reuse, or vice versa. Both remain available on
    the raw ``bundle`` for deterministic portfolio/decision consumers."""
    base = {
        "ticker": bundle.get("ticker"),
        "asset_type": bundle.get("asset_type"),
        "portfolio_context": {
            "weight_pct": (bundle.get("portfolio_context") or {}).get(
                "portfolio_weight_pct"
            ),
            "unrealized_gain_pct": (bundle.get("portfolio_context") or {}).get(
                "unrealized_gain_pct"
            ),
        },
        "missing_lanes": bundle.get("missing_lanes"),
        "degraded_lanes": bundle.get("degraded_lanes"),
    }
    if axis == AXIS_FUNDAMENTAL:
        base["fundamental"] = _compact_fundamental(bundle.get("fundamental"))
        base["valuation"] = bundle.get("valuation")
        base["sec"] = _payload_only(bundle.get("sec"))
    elif axis == AXIS_TECHNICAL:
        base["technical"] = bundle.get("technical")
    elif axis == AXIS_SENTIMENT:
        base["sentiment"] = bundle.get("sentiment")
        base["catalysts"] = _payload_only(bundle.get("catalysts"))
    elif axis == AXIS_RISK_FILING:
        base["sec"] = _payload_only(bundle.get("sec"))
        base["fundamental"] = _compact_fundamental(
            bundle.get("fundamental"),
            ratio_keys=_RISK_RATIO_KEYS, monetary_keys=_RISK_MONETARY_KEYS,
        )
    elif axis == AXIS_ETF_EXPOSURE:
        base["technical"] = bundle.get("technical")
        base["asset_specific"] = _payload_only(bundle.get("asset_specific"))
        base["fundamental"] = _compact_fundamental(bundle.get("fundamental"))
    elif axis == AXIS_CRYPTO_MARKET:
        base["asset_specific"] = bundle.get("asset_specific")
        base["technical"] = bundle.get("technical")
    return base


def _nonempty(value: Any) -> bool:
    if isinstance(value, dict):
        return any(v not in (None, "", [], {}) for v in value.values())
    if isinstance(value, list):
        return bool(value)
    return value not in (None, "", [], {})


def _axis_supplied_lanes(compact: dict[str, Any], axis: str) -> list[str]:
    """The exact nonempty external-evidence lanes actually represented in
    THIS axis's own compact bundle (contract §2) — never derived from the
    bundle-wide ``usable_lanes`` list, and never a superset of what the axis
    itself was actually given (e.g. ``risk_filing``'s narrowed fundamental
    subset counts only when THAT subset is nonempty, even if the full
    fundamentals lane succeeded). ``market``/price is never sent to any axis
    prompt (see ``_compact_bundle_for_axis``), so no axis ever claims
    LANE_PRICE lineage."""
    supplied: set[str] = set()
    if _nonempty(compact.get("technical")):
        supplied.add(LANE_TECHNICALS)
    if _nonempty(compact.get("fundamental")) or _nonempty(compact.get("valuation")):
        supplied.add(LANE_FUNDAMENTALS)
    if _nonempty(compact.get("sentiment")):
        supplied.add(LANE_NEWS_SENTIMENT)
    sec = compact.get("sec")
    if isinstance(sec, dict):
        if _nonempty(sec.get(LANE_SEC_COMPANY_FACTS)):
            supplied.add(LANE_SEC_COMPANY_FACTS)
        if _nonempty(sec.get(LANE_SEC_CATALYST)):
            supplied.add(LANE_SEC_CATALYST)
    # AXIS_SENTIMENT carries SEC catalyst evidence in its own compact
    # ``catalysts`` list rather than the ``sec`` dict (contract §2) — a
    # substantive catalyst artifact there must count as SEC_CATALYST being
    # supplied to this axis exactly as it would via the ``sec`` dict path.
    catalysts = compact.get("catalysts")
    if isinstance(catalysts, list) and any(_nonempty(c) for c in catalysts):
        supplied.add(LANE_SEC_CATALYST)
    asset_specific = compact.get("asset_specific")
    if isinstance(asset_specific, dict):
        if _nonempty(asset_specific.get("etf_fund_data")):
            supplied.add(LANE_ETF_FUND_DATA)
        if _nonempty(asset_specific.get("crypto_market")):
            supplied.add(LANE_CRYPTO_MARKET)

    candidate = set(source_lineage_v1.AXIS_CANDIDATE_LANES.get(axis, ()))
    return sorted(supplied & candidate)


def axis_evidence_context(bundle: dict[str, Any], axis: str) -> dict[str, Any]:
    """Single shared source of truth for "what evidence was actually
    supplied to this axis" (contract §2). Used IDENTICALLY by prompt
    construction, the specialist reuse lookup, the persisted
    ``input_fingerprint``, ``evidence_refs`` rebinding on reuse, and the
    bounded prompt-safe source projection — nobody derives supplied lanes, a
    lineage manifest, or a fingerprint any other way; there is exactly one
    prompt/fingerprint shape.

    Artifact-backed lanes only ever appear in ``compact_bundle`` (and
    therefore only ever count as supplied) when ``evidence_bundle_v1`` has
    already validated the parent artifact's ownership/ticker-scope/active/
    substantive-payload status — this function trusts that gate and never
    re-reads the database itself.
    """
    compact = _compact_bundle_for_axis(bundle, axis)
    supplied_lanes = _axis_supplied_lanes(compact, axis)
    manifest = source_lineage_v1.build_axis_lineage_manifest(
        axis=axis,
        source_refs_by_lane=bundle.get("source_refs_by_lane") or {},
        supplied_lanes=supplied_lanes,
    )
    # Compact, bounded source projection (contract §3) — lets the analysis
    # see the provenance of the evidence it was actually given. Deterministic
    # code output only; the LLM is never asked to invent or select a
    # citation.
    compact_with_sources = dict(compact)
    compact_with_sources["evidence_sources"] = source_lineage_v1.compact_projection(
        manifest["refs"]
    )
    # The ONE fingerprint for this axis: hashes exactly the object serialized
    # into the LLM prompt (``compact_with_sources``, including
    # ``evidence_sources``) minus only timestamps/cache markers/internal
    # storage identifiers (``_strip_volatile``) — nothing visible to the LLM
    # is excluded here, and nothing excluded here is visible to the LLM.
    input_fingerprint = stable_fingerprint(
        evidence_bundle_v1._strip_volatile(compact_with_sources)
    )
    return {
        "compact_bundle": compact_with_sources,
        "supplied_lanes": supplied_lanes,
        "manifest": manifest,
        "input_fingerprint": input_fingerprint,
    }


def _payload_only(value: Any) -> Any:
    """Drop artifact envelope noise (internal storage identifiers such as
    ``artifact_id``/``artifact_type``/``skill_pack``), keep payload substance
    (bounded) — wherever an artifact-summary shape (a dict carrying a
    ``payload`` key) appears: directly, nested inside a dict (e.g. the
    per-lane ``sec``/``asset_specific`` maps), or as an item inside a list
    (e.g. the ``catalysts`` list). Never sends internal artifact/database
    identifiers to the specialist prompt."""
    if isinstance(value, dict):
        if "payload" in value:
            return {
                "generated_at": value.get("generated_at"),
                "trust_level": value.get("trust_level"),
                "payload": value.get("payload"),
            }
        return {key: _payload_only(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_payload_only(v) for v in value][:3]
    return value


def validate_specialist_result(entry: Any) -> Optional[dict[str, Any]]:
    """Strict per-ticker validation. Returns normalized dict or None."""
    if not isinstance(entry, dict):
        return None
    ticker = str(entry.get("ticker") or "").strip().upper()
    stance = str(entry.get("stance") or "").strip().lower()
    if not ticker or stance not in ALLOWED_STANCES:
        return None
    try:
        score = float(entry.get("score"))
        confidence = float(entry.get("confidence"))
    except (TypeError, ValueError):
        return None
    if not (-1.0 <= score <= 1.0) or not (0.0 <= confidence <= 1.0):
        return None
    findings = entry.get("key_findings")
    if not isinstance(findings, list) or not findings:
        return None

    def _str_list(value: Any, cap: int) -> list[str]:
        if not isinstance(value, list):
            return []
        return [
            str(v)[:_MAX_STRING_CHARS]
            for v in value if isinstance(v, (str, int, float))
        ][:cap]

    return {
        "ticker": ticker,
        "stance": stance,
        "score": round(score, 4),
        "confidence": round(confidence, 4),
        "key_findings": _str_list(findings, _MAX_LIST_ITEMS),
        "risks": _str_list(entry.get("risks"), _MAX_LIST_ITEMS),
        "missing_evidence": _str_list(entry.get("missing_evidence"), _MAX_LIST_ITEMS),
        "limitations": _str_list(entry.get("limitations"), _MAX_LIST_ITEMS),
    }


def _build_user_prompt(axis: str, bundles: list[dict[str, Any]]) -> str:
    import json

    focus = _AXIS_FOCUS.get(axis, axis)
    compact = [
        axis_evidence_context(bundle, axis)["compact_bundle"] for bundle in bundles
    ]
    tickers = [str(b.get("ticker")) for b in compact]
    return (
        f"Specialist axis: {axis}. Focus: {focus}.\n"
        f"Analyze these tickers: {', '.join(tickers)}.\n"
        "Evidence bundles (JSON):\n"
        + json.dumps(compact, default=str)[:60000]
    )


class SpecialistBatchOutcome:
    def __init__(self):
        self.persisted: list[str] = []
        self.reused: list[str] = []
        self.skipped_insufficient: list[str] = []
        self.malformed: list[str] = []
        self.llm_calls = 0
        # Tickers this task was asked to cover (observability only — the
        # batch_key already carries this; kept here so callers don't need to
        # re-parse it just to log a requested count).
        self.requested_tickers: list[str] = []
        # Repair calls made for missing/malformed tickers after the initial
        # batch call (bounded — see execute_specialist_task).
        self.repair_calls = 0
        # Calls (initial or repair) where the raw LLM response looked
        # truncated (fenced/verbose/cut-off Haiku output).
        self.truncated_calls = 0
        # Calls that failed with a non-retryable provider error (quota /
        # authentication) — these never trigger a repair call.
        self.quota_or_auth_failures = 0
        self.error: Optional[str] = None
        # Actual model(s) that answered each ask_json call (per-session cost
        # metrics by model; best-effort observability only, never decision
        # input). One entry per LLM call, in call order.
        self.models_used: list[str] = []

    @property
    def final_state(self) -> str:
        if self.error:
            return TASK_FAILED_RETRYABLE
        if self.malformed or self.skipped_insufficient:
            return TASK_DEGRADED
        return TASK_SUCCEEDED

    @property
    def partial_success(self) -> bool:
        """At least one ticker persisted AND at least one requested ticker
        did not (malformed/insufficient evidence) — observability only."""
        return bool(self.persisted) and bool(self.malformed or self.skipped_insufficient)


def _axis_has_evidence(bundle: dict[str, Any], axis: str) -> bool:
    """Cost control: no LLM call for a ticker with no usable axis evidence.

    Checked against the axis's own compact prompt projection — ``market`` is
    never part of it (contract §5), so a ticker with price data alone but no
    technical/fundamental/sentiment substance is correctly insufficient: the
    LLM would otherwise be asked to analyze evidence it never actually sees.
    """
    compact = _compact_bundle_for_axis(bundle, axis)
    for key in ("fundamental", "technical", "sentiment", "sec", "asset_specific",
                "catalysts", "valuation"):
        value = compact.get(key)
        if isinstance(value, dict) and any(
            v not in (None, "", [], {}) for v in value.values()
        ):
            return True
        if isinstance(value, list) and value:
            return True
    return False


async def execute_specialist_task(
    client: Any,
    *,
    task: dict[str, Any],
    llm: Any,
    now: Optional[datetime] = None,
) -> SpecialistBatchOutcome:
    """Execute one specialist batch task: bundles → one LLM call → per-ticker
    durable outputs. ``llm`` is an ``LLMClient``-shaped object (ask_json)."""
    now = now or _now()
    outcome = SpecialistBatchOutcome()
    session_id = str(task.get("run_session_id") or "")
    user_id = str(task.get("user_id") or "")
    axis = str(task.get("lane") or "")
    batch_tickers = parse_batch_tickers(str(task.get("batch_key") or ""))
    if not batch_tickers:
        outcome.error = "empty_batch_key"
        return outcome
    outcome.requested_tickers = list(batch_tickers)

    # Claim fence (pre-work): don't even start LLM work on a lost claim.
    if not store.owns_claim(client, task):
        outcome.error = "claim_lost"
        return outcome

    ticker_rows = {
        str(r.get("ticker") or ""): r
        for r in store.list_ticker_rows(client, run_session_id=session_id)
    }

    to_analyze: list[dict[str, Any]] = []
    fingerprints: dict[str, str] = {}
    for ticker in batch_tickers:
        row = ticker_rows.get(ticker)
        bundle = (row or {}).get("evidence_bundle")
        if not isinstance(bundle, dict) or not bundle:
            outcome.skipped_insufficient.append(ticker)
            continue
        context = axis_evidence_context(bundle, axis)
        fingerprint = context["input_fingerprint"]
        fingerprints[ticker] = fingerprint

        # Reuse an unchanged prior output instead of a new LLM call. Reuse
        # requires the CURRENT prompt version — a row persisted under an
        # older (unsourced) prompt contract never matches and is never
        # reused (contract §4/§13).
        if fingerprint:
            reusable = store.find_reusable_specialist_output(
                client,
                user_id=user_id,
                ticker=ticker,
                axis=axis,
                input_fingerprint=fingerprint,
                prompt_version=PROMPT_VERSION,
                now=now,
            )
            if reusable is not None and str(
                reusable.get("run_session_id")
            ) != session_id:
                # Analytical fields only carry over; evidence_refs is REBUILT
                # from THIS session's own bundle lineage via the SAME shared
                # helper the initial analysis and prompt use — a reused
                # result must never carry forward a prior session's
                # (possibly stale) source references (contract §4).
                rebuilt_refs = context["manifest"]
                store.upsert_specialist_output(
                    client,
                    run_session_id=session_id,
                    user_id=user_id,
                    ticker=ticker,
                    axis=axis,
                    output={
                        key: reusable.get(key)
                        for key in (
                            "stance", "score", "confidence", "key_findings",
                            "risks", "missing_evidence", "limitations",
                            "valid_until", "model",
                        )
                    } | {
                        "evidence_refs": rebuilt_refs,
                        "prompt_version": PROMPT_VERSION,
                        "input_fingerprint": fingerprint,
                        "batch_key": str(task.get("batch_key") or ""),
                    },
                    now=now,
                )
                outcome.reused.append(ticker)
                continue

        if not _axis_has_evidence(bundle, axis):
            outcome.skipped_insufficient.append(ticker)
            continue
        to_analyze.append(bundle)

    if not to_analyze:
        return outcome

    system = SPECIALIST_SYSTEM_PROMPT
    requested = [str(b.get("ticker")).upper() for b in to_analyze]
    bundle_by_ticker = {str(b.get("ticker")).upper(): b for b in to_analyze}

    async def _call(prompt: str, tickers_in_call: list[str]) -> tuple[dict[str, Any], str]:
        outcome.llm_calls += 1
        call_meta: dict[str, Any] = {"axis": axis, "run_session_id": session_id}
        budget = _specialist_token_budget(len(tickers_in_call))
        response = await llm.ask_json(
            system, prompt, max_tokens=budget,
            metadata=call_meta, reject_prose=True,
            # The specialist executor owns its OWN bounded, per-ticker repair
            # strategy below — LLMClient must never silently repeat this same
            # prompt/batch internally on a truncated response (that would
            # double an already-budgeted call, invisible to outcome.llm_calls
            # and to the ≤3-calls-per-two-ticker-task bound).
            retry_truncated_response=False,
            # The DURABLE task's own retry/backoff owns retrying a transport
            # failure (rate-limit/timeout/transient/quota/auth) — a single
            # ask_json() call must cost exactly one actual provider call,
            # never LLMClient's own internal 4-attempt backoff loop, or a
            # single specialist call could burn up to 4 provider calls
            # before the specialist-level provider-failure guard even runs.
            primary_max_attempts=1,
        )
        model_used = call_meta.get("model_used")
        if model_used:
            outcome.models_used.append(str(model_used))
        if (
            call_meta.get("primary_truncated_response_detected")
            or call_meta.get("retry_truncated_response_detected")
        ):
            outcome.truncated_calls += 1
        error_class = str(call_meta.get("error_classification") or "")
        return (response if isinstance(response, dict) else {}), error_class

    def _validate_batch(
        response: dict[str, Any], expected: list[str]
    ) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for entry in (response.get("results") or []):
            normalized = validate_specialist_result(entry)
            if normalized is not None and normalized["ticker"] in expected:
                out[normalized["ticker"]] = normalized
        return out

    user_prompt = _build_user_prompt(axis, to_analyze)
    response, error_class = await _call(user_prompt, requested)
    validated: dict[str, dict[str, Any]] = _validate_batch(response, requested)

    # Any ACTUAL provider-call failure — quota/authentication, or an
    # exhausted rate-limit/transient retry inside LLMClient — is never
    # reinterpreted as ticker-level malformed JSON. A parse/truncation
    # failure (the provider answered, but the JSON was bad) has NO
    # classification here and is eligible for the bounded per-ticker
    # repair loop below; a genuine provider-call failure gets zero repair
    # calls and an honest retryable task outcome instead.
    quota_or_auth = error_class in NON_RETRYABLE_PROVIDER_CLASSES
    provider_failure = bool(error_class)
    if provider_failure:
        if quota_or_auth:
            outcome.quota_or_auth_failures += 1
        # One provider call only — never a repair call. The durable task
        # retry backoff owns the next attempt.
    else:
        # Retry only missing/malformed tickers, one call PER ticker (bounded:
        # a two-ticker batch never exceeds 1 initial + 2 individual = 3 total
        # calls). This also guarantees an already-validated ticker is never
        # re-requested, and a peer ticker's failure never re-runs a
        # successfully validated ticker.
        missing = [t for t in requested if t not in validated]
        for ticker in missing:
            repair_bundle = bundle_by_ticker.get(ticker)
            if repair_bundle is None:
                continue
            repair_prompt = (
                f"Your previous response was missing or malformed for: {ticker}. "
                "Return STRICT JSON for ONLY this ticker.\n"
                + _build_user_prompt(axis, [repair_bundle])
            )
            repair_response, repair_error_class = await _call(repair_prompt, [ticker])
            outcome.repair_calls += 1
            validated.update(_validate_batch(repair_response, [ticker]))
            if repair_error_class:
                quota_or_auth = repair_error_class in NON_RETRYABLE_PROVIDER_CLASSES
                if quota_or_auth:
                    outcome.quota_or_auth_failures += 1
                provider_failure = True
                break  # stop further per-ticker repairs on any provider failure

    if not validated and requested:
        # Whole-call failure (LLM down / empty / provider failure) — retry
        # the durable task, keep nothing. Never fires once ANY ticker
        # validated — a peer's success is never discarded.
        if quota_or_auth:
            outcome.error = "specialist_provider_quota_or_auth_failure"
        elif provider_failure:
            outcome.error = "specialist_provider_call_failed"
        else:
            outcome.error = "specialist_llm_call_failed"
        return outcome

    # Claim fence (post-LLM, pre-write): the LLM call is the long-running
    # stretch where a lease most plausibly expires. A stale worker whose task
    # was reclaimed must not overwrite the rival claim's persisted outputs.
    if not store.owns_claim(client, task):
        outcome.error = "claim_lost"
        return outcome

    valid_until = (now + timedelta(hours=OUTPUT_VALID_HOURS)).isoformat()
    model_name = (
        outcome.models_used[-1] if outcome.models_used
        else getattr(llm, "primary_model", None)
        or getattr(llm, "model", None)
        or "claude"
    )
    for bundle in to_analyze:
        ticker = str(bundle.get("ticker")).upper()
        result = validated.get(ticker)
        if result is None:
            outcome.malformed.append(ticker)
            continue
        axis_lineage = axis_evidence_context(bundle, axis)["manifest"]
        persisted = store.upsert_specialist_output(
            client,
            run_session_id=session_id,
            user_id=user_id,
            ticker=ticker,
            axis=axis,
            output={
                "stance": result["stance"],
                "score": result["score"],
                "confidence": result["confidence"],
                "key_findings": result["key_findings"],
                "risks": result["risks"],
                "evidence_refs": axis_lineage,
                "missing_evidence": result["missing_evidence"],
                "limitations": result["limitations"],
                "valid_until": valid_until,
                "model": str(model_name),
                "prompt_version": PROMPT_VERSION,
                "input_fingerprint": fingerprints.get(ticker, ""),
                "batch_key": str(task.get("batch_key") or ""),
            },
            now=now,
        )
        if persisted:
            outcome.persisted.append(ticker)
        else:
            outcome.malformed.append(ticker)
    return outcome


# ── Deterministic conflict resolution ────────────────────────────────────────
#
# Replaces the deleted conditional review LLM. Zero provider/LLM calls: the
# directional specialist signal is neutralized to HOLD and upstream
# confidence is capped — conflict_policy_v1 is the single deterministic
# authority for both the trigger and the resolution. The canonical
# decision_policy_v1.decide() remains the only visible-action authority.


async def execute_conflict_resolution_task(
    client: Any,
    *,
    task: dict[str, Any],
    now: Optional[datetime] = None,
) -> SpecialistBatchOutcome:
    """Resolve conflicting specialist outputs for one ticker deterministically
    (advisory audit row only — never a visible action)."""
    now = now or _now()
    outcome = SpecialistBatchOutcome()
    session_id = str(task.get("run_session_id") or "")
    user_id = str(task.get("user_id") or "")
    ticker = str(task.get("ticker") or "").upper()

    # Claim fence (pre-work).
    if not store.owns_claim(client, task):
        outcome.error = "claim_lost"
        return outcome

    # The ONE strict specialist-input authority — same function the trigger,
    # the fingerprint and decision-time validation all use.
    raw_outputs = store.list_specialist_outputs(
        client, run_session_id=session_id, ticker=ticker,
    )
    reviewed_inputs = conflict_policy_v1.normalize_valid_inputs(raw_outputs)

    ticker_row = next(
        (
            r for r in store.list_ticker_rows(client, run_session_id=session_id)
            if str(r.get("ticker") or "") == ticker
        ),
        None,
    )
    weight_pct = (ticker_row or {}).get("portfolio_weight_pct")

    # Fail closed: a durable conflict task exists, but the CURRENT immutable
    # inputs no longer meet the conflict contract (recomputed via the same
    # single authority the scheduler used to create this task).
    assessment = conflict_policy_v1.assess_conflict(reviewed_inputs, weight_pct)
    if not assessment["conflict_detected"]:
        outcome.error = "conflict_task_without_conflict"
        return outcome

    # Deterministic audit/lineage inputs — reuses the SAME normalized-input
    # and lineage helpers the prior LLM review used, now purely as audit
    # material (no LLM ever sees them).
    prompt_inputs = source_lineage_v1.build_review_prompt_context(
        reviewed_inputs, ticker=ticker,
    )
    review_lineage = source_lineage_v1.build_review_lineage_manifest(
        reviewed_inputs, ticker=ticker,
    )
    # Same fingerprint function the decision reader recomputes — covers
    # ticker, schema version, the exact bounded lineage input, the
    # assessment, and major-position state, so staleness is detectable.
    input_fingerprint = conflict_policy_v1.conflict_fingerprint(
        ticker=ticker, prompt_context=prompt_inputs, assessment=assessment,
        major=conflict_policy_v1.safe_major_position(weight_pct),
    )
    summary_sentence = conflict_policy_v1.conflict_summary_sentence(assessment)

    # Claim fence (recheck before persistence).
    if not store.owns_claim(client, task):
        outcome.error = "claim_lost"
        return outcome

    persisted = store.upsert_specialist_output(
        client,
        run_session_id=session_id,
        user_id=user_id,
        ticker=ticker,
        axis=AXIS_REVIEW,
        output={
            "stance": "neutral",
            "score": 0.0,
            "confidence": conflict_policy_v1.CONFLICT_CONFIDENCE_CAP,
            "key_findings": [summary_sentence],
            "risks": [
                "Conflicting specialist evidence increases the risk of "
                "acting prematurely.",
            ],
            "missing_evidence": [],
            "limitations": [
                "Directional signal neutralized until the evidence "
                "becomes more consistent.",
            ],
            "evidence_refs": review_lineage,
            "valid_until": (now + timedelta(hours=OUTPUT_VALID_HOURS)).isoformat(),
            "model": conflict_policy_v1.SCHEMA_VERSION,
            "prompt_version": conflict_policy_v1.SCHEMA_VERSION,
            "input_fingerprint": input_fingerprint,
            "batch_key": None,
        },
        now=now,
    )
    if persisted:
        outcome.persisted.append(ticker)
    else:
        outcome.error = "conflict_resolution_persist_failed"
    return outcome
