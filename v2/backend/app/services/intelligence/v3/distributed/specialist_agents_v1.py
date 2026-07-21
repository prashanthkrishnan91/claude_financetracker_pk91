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

Boundary proof: this module intentionally imports NOTHING from
``agents.data_sources``, ``ai.io_layer``, ``market_data`` or the research
workers — the architecture-boundary test asserts that.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from . import run_task_store_v1 as store
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
    TASK_DEGRADED,
    TASK_SUCCEEDED,
)
from .run_task_store_v1 import TASK_FAILED_RETRYABLE

logger = logging.getLogger(__name__)

PROMPT_VERSION = "distributed_specialist_v1"
# How long a specialist output stays reusable for an unchanged evidence
# fingerprint (skips duplicate LLM calls across sessions).
OUTPUT_VALID_HOURS = 24.0

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
decisions — a deterministic policy engine owns those. Do not output action
words as recommendations.

Return STRICT JSON only (no markdown fences, no prose) with this exact shape:
{"results": [{
  "ticker": "...",
  "stance": "positive" | "neutral" | "negative",
  "score": <float -1.0..1.0>,
  "confidence": <float 0.0..1.0>,
  "key_findings": ["...", ...],   // 1-4 short evidence-grounded findings
  "risks": ["...", ...],          // 0-3 short risks
  "missing_evidence": ["...", ...],
  "limitations": ["...", ...]
}]}
One entry per requested ticker, in any order. Every requested ticker MUST
appear exactly once."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _compact_bundle_for_axis(bundle: dict[str, Any], axis: str) -> dict[str, Any]:
    """Trim the bundle to what the axis needs (prompt-size control)."""
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
            "prior_action": (bundle.get("portfolio_context") or {}).get(
                "prior_action"
            ),
        },
        "market": bundle.get("market"),
        "missing_lanes": bundle.get("missing_lanes"),
        "degraded_lanes": bundle.get("degraded_lanes"),
    }
    if axis == AXIS_FUNDAMENTAL:
        base["fundamental"] = bundle.get("fundamental")
        base["valuation"] = bundle.get("valuation")
        base["sec"] = _payload_only(bundle.get("sec"))
    elif axis == AXIS_TECHNICAL:
        base["technical"] = bundle.get("technical")
    elif axis == AXIS_SENTIMENT:
        base["sentiment"] = bundle.get("sentiment")
        base["catalysts"] = _payload_only(bundle.get("catalysts"))
    elif axis == AXIS_RISK_FILING:
        base["sec"] = _payload_only(bundle.get("sec"))
        base["fundamental"] = {
            k: (bundle.get("fundamental") or {}).get(k)
            for k in ("debt_to_equity", "total_debt", "cash", "beta")
        }
    elif axis == AXIS_ETF_EXPOSURE:
        base["technical"] = bundle.get("technical")
        base["asset_specific"] = _payload_only(bundle.get("asset_specific"))
        base["fundamental"] = bundle.get("fundamental")
    elif axis == AXIS_CRYPTO_MARKET:
        base["asset_specific"] = bundle.get("asset_specific")
        base["technical"] = bundle.get("technical")
    return base


def _payload_only(value: Any) -> Any:
    """Drop artifact envelope noise, keep payload substance (bounded)."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if isinstance(item, dict) and "payload" in item:
                out[key] = {
                    "generated_at": item.get("generated_at"),
                    "trust_level": item.get("trust_level"),
                    "payload": item.get("payload"),
                }
            else:
                out[key] = item
        return out
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
        return [str(v)[:300] for v in value if isinstance(v, (str, int, float))][:cap]

    return {
        "ticker": ticker,
        "stance": stance,
        "score": round(score, 4),
        "confidence": round(confidence, 4),
        "key_findings": _str_list(findings, 4),
        "risks": _str_list(entry.get("risks"), 3),
        "missing_evidence": _str_list(entry.get("missing_evidence"), 5),
        "limitations": _str_list(entry.get("limitations"), 3),
    }


def _build_user_prompt(axis: str, bundles: list[dict[str, Any]]) -> str:
    import json

    focus = _AXIS_FOCUS.get(axis, axis)
    compact = [
        _compact_bundle_for_axis(bundle, axis) for bundle in bundles
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
        self.error: Optional[str] = None

    @property
    def final_state(self) -> str:
        if self.error:
            return TASK_FAILED_RETRYABLE
        if self.malformed or self.skipped_insufficient:
            return TASK_DEGRADED
        return TASK_SUCCEEDED


def _axis_has_evidence(bundle: dict[str, Any], axis: str) -> bool:
    """Cost control: no LLM call for a ticker with no usable axis evidence."""
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
    # Price-only axes (etf_exposure fallback) still count when market exists.
    market = compact.get("market")
    if axis == AXIS_ETF_EXPOSURE and isinstance(market, dict) and market:
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
        fingerprint = str(bundle.get("input_fingerprint") or "")
        fingerprints[ticker] = fingerprint

        # Reuse an unchanged prior output instead of a new LLM call.
        if fingerprint:
            reusable = store.find_reusable_specialist_output(
                client,
                user_id=user_id,
                ticker=ticker,
                axis=axis,
                input_fingerprint=fingerprint,
                now=now,
            )
            if reusable is not None and str(
                reusable.get("run_session_id")
            ) != session_id:
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
                            "risks", "evidence_refs", "missing_evidence",
                            "limitations", "valid_until", "model",
                            "prompt_version", "input_fingerprint",
                        )
                    } | {"batch_key": str(task.get("batch_key") or "")},
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
    user_prompt = _build_user_prompt(axis, to_analyze)
    requested = [str(b.get("ticker")).upper() for b in to_analyze]

    async def _call(prompt: str) -> dict[str, Any]:
        outcome.llm_calls += 1
        response = await llm.ask_json(
            system, prompt, max_tokens=350 * max(1, len(requested)),
            metadata={"axis": axis, "run_session_id": session_id},
        )
        return response if isinstance(response, dict) else {}

    response = await _call(user_prompt)
    validated: dict[str, dict[str, Any]] = {}
    for entry in (response.get("results") or []):
        normalized = validate_specialist_result(entry)
        if normalized is not None and normalized["ticker"] in requested:
            validated[normalized["ticker"]] = normalized

    missing = [t for t in requested if t not in validated]
    if missing:
        # One bounded repair retry for only the missing/malformed tickers.
        repair_bundles = [
            b for b in to_analyze if str(b.get("ticker")).upper() in missing
        ]
        repair_prompt = (
            "Your previous response was missing or malformed for: "
            f"{', '.join(missing)}. Return STRICT JSON for ONLY these tickers.\n"
            + _build_user_prompt(axis, repair_bundles)
        )
        repair_response = await _call(repair_prompt)
        for entry in (repair_response.get("results") or []):
            normalized = validate_specialist_result(entry)
            if normalized is not None and normalized["ticker"] in missing:
                validated[normalized["ticker"]] = normalized

    if not validated and requested:
        # Whole-call failure (LLM down / empty) — retry the task, keep nothing.
        outcome.error = "specialist_llm_call_failed"
        return outcome

    valid_until = (now + timedelta(hours=OUTPUT_VALID_HOURS)).isoformat()
    model_name = getattr(llm, "primary_model", None) or getattr(
        llm, "model", None
    ) or "claude"
    for bundle in to_analyze:
        ticker = str(bundle.get("ticker")).upper()
        result = validated.get(ticker)
        if result is None:
            outcome.malformed.append(ticker)
            continue
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
                "evidence_refs": list(bundle.get("source_refs") or []),
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


# ── Conditional review agent ─────────────────────────────────────────────────

REVIEW_SYSTEM_PROMPT = """You are a senior investment research reviewer.
You receive several specialist research outputs for ONE ticker that disagree
or carry low confidence. Reconcile them using ONLY the provided outputs and
their cited evidence references. Do not fetch data, do not invent evidence,
and do NOT make a buy/hold/trim/sell decision — a deterministic policy engine
owns actions.

Return STRICT JSON only:
{"ticker": "...", "stance": "positive"|"neutral"|"negative",
 "score": <float -1..1>, "confidence": <float 0..1>,
 "key_findings": ["which specialist view is better supported and why", ...],
 "risks": ["..."], "missing_evidence": ["..."], "limitations": ["..."]}"""


async def execute_review_task(
    client: Any,
    *,
    task: dict[str, Any],
    llm: Any,
    now: Optional[datetime] = None,
) -> SpecialistBatchOutcome:
    """Reconcile conflicting specialist outputs for one ticker (advisory)."""
    import json

    now = now or _now()
    outcome = SpecialistBatchOutcome()
    session_id = str(task.get("run_session_id") or "")
    user_id = str(task.get("user_id") or "")
    ticker = str(task.get("ticker") or "").upper()

    outputs = [
        {
            "axis": o.get("axis"),
            "stance": o.get("stance"),
            "score": o.get("score"),
            "confidence": o.get("confidence"),
            "key_findings": o.get("key_findings"),
            "risks": o.get("risks"),
            "evidence_refs": o.get("evidence_refs"),
        }
        for o in store.list_specialist_outputs(
            client, run_session_id=session_id, ticker=ticker,
        )
        if o.get("axis") != AXIS_REVIEW
    ]
    if len(outputs) < 1:
        outcome.skipped_insufficient.append(ticker)
        return outcome

    outcome.llm_calls += 1
    response = await llm.ask_json(
        REVIEW_SYSTEM_PROMPT,
        f"Ticker: {ticker}\nSpecialist outputs (JSON):\n"
        + json.dumps(outputs, default=str)[:30000],
        max_tokens=500,
        metadata={"axis": AXIS_REVIEW, "run_session_id": session_id},
    )
    normalized = validate_specialist_result(
        {**response, "ticker": ticker} if isinstance(response, dict) else None
    )
    if normalized is None:
        outcome.error = "review_llm_call_failed"
        return outcome

    model_name = getattr(llm, "primary_model", None) or "claude"
    persisted = store.upsert_specialist_output(
        client,
        run_session_id=session_id,
        user_id=user_id,
        ticker=ticker,
        axis=AXIS_REVIEW,
        output={
            "stance": normalized["stance"],
            "score": normalized["score"],
            "confidence": normalized["confidence"],
            "key_findings": normalized["key_findings"],
            "risks": normalized["risks"],
            "evidence_refs": [],
            "missing_evidence": normalized["missing_evidence"],
            "limitations": normalized["limitations"],
            "valid_until": (now + timedelta(hours=OUTPUT_VALID_HOURS)).isoformat(),
            "model": str(model_name),
            "prompt_version": PROMPT_VERSION,
            "input_fingerprint": "",
            "batch_key": None,
        },
        now=now,
    )
    if persisted:
        outcome.persisted.append(ticker)
    else:
        outcome.error = "review_persist_failed"
    return outcome
