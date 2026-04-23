"""Phase 5 — cost and failure control layer.

Classifies each orchestrator run into FULL or DEGRADED mode and tracks
LLM cost per run so the dashboard can answer "how much did this cost?"
and "how much did degraded mode save?".

Design invariants:
  * Pure classifier + tracker. No IO, no LLM, no randomness.
  * DEGRADED mode guarantees the acceptance-gate cost reduction: zero
    per-ticker LLM calls (saves N calls on an N-ticker book) and a
    deterministic synthesis path (saves the synthesis call too).
  * Every verdict returned in DEGRADED mode is still a validated
    AnalystVerdict — just the deterministic HOLD / INSUFFICIENT_DATA
    flavour. Never an empty dict.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Iterable, Optional

from .market_snapshot import MarketSnapshot
from .per_ticker_analyst import AnalystVerdict

logger = logging.getLogger(__name__)


# Threshold from the Phase 5 spec. Average ``data_quality_score`` below
# this flips the run into DEGRADED mode and skips the per-ticker LLM.
DEGRADED_QUALITY_THRESHOLD = 0.5


class RunMode(str, Enum):
    FULL = "FULL"
    DEGRADED = "DEGRADED"


@dataclass
class ModeDecision:
    """Outcome of the run-mode classifier.

    ``reason`` is a short machine-readable tag; ``explanation`` is
    human-facing prose suitable for embedding in the portfolio
    summary or a DEGRADED-badge tooltip.
    """

    mode: RunMode
    avg_quality: float
    insufficient_count: int
    total_tickers: int
    reason: str
    explanation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "avg_quality": round(self.avg_quality, 3),
            "insufficient_count": self.insufficient_count,
            "total_tickers": self.total_tickers,
            "reason": self.reason,
            "explanation": self.explanation,
        }


def classify_run_mode(
    snapshots: Iterable[MarketSnapshot],
    *,
    threshold: float = DEGRADED_QUALITY_THRESHOLD,
) -> ModeDecision:
    """Return the :class:`ModeDecision` for a collection of snapshots.

    DEGRADED when ``avg(data_quality_score) < threshold``. Also triggers
    when more than half the portfolio lacks a live price (``price_source
    != "live"``) — that's an equivalent signal the data layer is thin
    enough that per-ticker LLM calls would burn tokens on noise.
    """
    snaps = list(snapshots)
    if not snaps:
        return ModeDecision(
            mode=RunMode.DEGRADED,
            avg_quality=0.0,
            insufficient_count=0,
            total_tickers=0,
            reason="no_snapshots",
            explanation="No market snapshots available — operating in "
                        "DEGRADED mode until the data layer recovers.",
        )

    total = len(snaps)
    avg_quality = sum(s.data_quality_score for s in snaps) / total
    insufficient = sum(1 for s in snaps if s.data_quality_score < threshold)
    non_live = sum(1 for s in snaps if s.price_source != "live")
    sentiment_covered = sum(1 for s in snaps if (s.sentiment_label or "").lower() not in {"", "unavailable"})
    fundamentals_covered = sum(1 for s in snaps if bool(s.fundamentals))
    evidence_coverage = (sentiment_covered + fundamentals_covered) / max(1, (2 * total))

    if avg_quality < threshold and evidence_coverage < 0.55:
        return ModeDecision(
            mode=RunMode.DEGRADED,
            avg_quality=avg_quality,
            insufficient_count=insufficient,
            total_tickers=total,
            reason="avg_quality_below_threshold",
            explanation=(
                f"DEGRADED mode: average data-quality score "
                f"{avg_quality:.2f} below {threshold:.2f} threshold — "
                "per-ticker LLM calls skipped, deterministic HOLD "
                "verdicts issued."
            ),
        )
    if avg_quality < threshold:
        return ModeDecision(
            mode=RunMode.FULL,
            avg_quality=avg_quality,
            insufficient_count=insufficient,
            total_tickers=total,
            reason="core_evidence_still_strong",
            explanation=(
                f"FULL mode retained: quality score {avg_quality:.2f} is below "
                f"threshold but evidence coverage remains {evidence_coverage:.0%}."
            ),
        )

    if non_live * 2 > total:
        return ModeDecision(
            mode=RunMode.DEGRADED,
            avg_quality=avg_quality,
            insufficient_count=insufficient,
            total_tickers=total,
            reason="majority_stale_prices",
            explanation=(
                f"DEGRADED mode: {non_live}/{total} tickers lack live "
                "prices — operating with cached snapshots until upstream "
                "feeds recover."
            ),
        )

    return ModeDecision(
        mode=RunMode.FULL,
        avg_quality=avg_quality,
        insufficient_count=insufficient,
        total_tickers=total,
        reason="ok",
        explanation=(
            f"Data quality {avg_quality:.2f} — FULL mode: per-ticker "
            "analyst + portfolio synthesis both active."
        ),
    )


# ── Deterministic DEGRADED-mode verdicts ───────────────────────────────────


def build_degraded_verdicts(
    snapshots: dict[str, MarketSnapshot],
    *,
    decision: ModeDecision,
) -> dict[str, AnalystVerdict]:
    """Produce one deterministic HOLD / INSUFFICIENT_DATA verdict per ticker.

    Called instead of ``analyze_portfolio`` when :func:`classify_run_mode`
    returns DEGRADED. Guarantees every ticker still has a populated
    verdict (never ``{}``) and that the total LLM call count for the
    run stays at ≤ 1 (only synthesis may run, and even that falls back
    to deterministic output).
    """
    verdicts: dict[str, AnalystVerdict] = {}
    for ticker, snap in snapshots.items():
        if snap.data_quality_score < 0.25 or snap.price is None:
            verdicts[ticker] = AnalystVerdict(
                ticker=ticker,
                action="INSUFFICIENT_DATA",
                conviction=0.0,
                key_drivers=[],
                risks=["Degraded data layer — no reliable signal"],
                confidence=0.0,
                used_fallback=True,
                error=f"degraded_mode:{decision.reason}",
            )
            continue
        drivers = _degraded_drivers(snap)
        risks = _degraded_risks(snap, decision=decision)
        verdicts[ticker] = AnalystVerdict(
            ticker=ticker,
            action="HOLD",
            conviction=0.0,
            key_drivers=drivers,
            risks=risks,
            confidence=max(0.0, min(0.3, snap.data_quality_score)),
            used_fallback=True,
            error=f"degraded_mode:{decision.reason}",
        )
    return verdicts


def build_full_mode_verdicts(
    snapshots: dict[str, MarketSnapshot],
    *,
    features: dict[str, Any],
) -> dict[str, AnalystVerdict]:
    """Deterministic per-ticker verdicts for FULL mode.

    Keeps the pipeline stable and cheap by avoiding per-ticker LLM calls while
    still producing structured BUY/HOLD/REDUCE output grounded in snapshot +
    feature data only.
    """
    verdicts: dict[str, AnalystVerdict] = {}
    for ticker, snap in snapshots.items():
        fs = features.get(ticker)
        quality = max(0.0, min(1.0, float(snap.data_quality_score or 0.0)))
        if snap.price is None or quality < 0.25 or fs is None:
            verdicts[ticker] = AnalystVerdict(
                ticker=ticker,
                action="INSUFFICIENT_DATA",
                conviction=0.0,
                key_drivers=[],
                risks=["Insufficient structured inputs for a reliable call"],
                confidence=0.0,
                used_fallback=True,
                error="deterministic_insufficient_data",
            )
            continue

        momentum = float(getattr(fs, "momentum_score", 0.0) or 0.0)
        trend = str(getattr(fs, "trend_regime", "range") or "range")
        sentiment = float(snap.sentiment_score or 0.0)
        rs_label = str(getattr(fs, "relative_strength_label", "neutral") or "neutral")

        raw_score = 0.55 * momentum + 0.25 * sentiment
        if trend == "uptrend":
            raw_score += 0.15
        elif trend == "downtrend":
            raw_score -= 0.15
        if rs_label == "strong":
            raw_score += 0.10
        elif rs_label == "weak":
            raw_score -= 0.10

        score = max(-1.0, min(1.0, raw_score))
        conviction = round(min(abs(score), 0.85) * quality, 2)
        confidence = round(min(0.95, max(0.20, 0.35 + quality * 0.5)), 2)

        if score >= 0.30 and quality >= 0.45:
            action = "BUY"
        elif score <= -0.30 and quality >= 0.55:
            action = "REDUCE"
        else:
            action = "HOLD"
            conviction = min(conviction, 0.35)

        drivers: list[str] = []
        if snap.return_30d is not None:
            sign = "+" if snap.return_30d >= 0 else ""
            drivers.append(f"30d return {sign}{snap.return_30d:.1f}%")
        drivers.append(f"Trend regime: {trend}")
        drivers.append(f"Relative strength: {rs_label}")

        risks: list[str] = []
        if snap.missing_fields:
            risks.append(f"Missing fields: {', '.join(snap.missing_fields[:2])}")
        if trend == "downtrend":
            risks.append("Trend remains weak; avoid aggressive sizing")
        elif quality < 0.60:
            risks.append("Signal confidence capped by partial data coverage")

        verdicts[ticker] = AnalystVerdict(
            ticker=ticker,
            action=action,
            conviction=round(conviction, 2),
            key_drivers=drivers[:3],
            risks=risks[:2],
            confidence=confidence,
            used_fallback=True,
            error="deterministic_full_mode",
        )
    return verdicts


def _degraded_drivers(snap: MarketSnapshot) -> list[str]:
    """Two deterministic drivers from snapshot fields so cards don't clone."""
    drivers: list[str] = []
    if snap.return_30d is not None:
        sign = "+" if snap.return_30d >= 0 else ""
        drivers.append(f"30d return {sign}{snap.return_30d:.1f}% (cached)")
    if snap.sector:
        drivers.append(f"Sector: {snap.sector}")
    if snap.price_source != "live":
        drivers.append(f"Price source: {snap.price_source}")
    return drivers[:3] or ["Deterministic HOLD — analyst skipped in DEGRADED mode"]


def _degraded_risks(snap: MarketSnapshot, *, decision: ModeDecision) -> list[str]:
    risks: list[str] = []
    if snap.missing_fields:
        risks.append(f"Missing: {', '.join(snap.missing_fields[:2])}")
    risks.append(f"Run mode DEGRADED ({decision.reason})")
    return risks[:2]


# ── Cost tracker ───────────────────────────────────────────────────────────
# Rates in USD per 1M tokens. Updated periodically; ops can override via
# env if the provider changes pricing. These are deliberate approximations
# — the dashboard rounds to cents, so an estimate within ~10% is fine.

_TOKEN_RATES: dict[str, tuple[float, float]] = {
    # model → (input $/1M, output $/1M)
    "claude-sonnet-4-6":        (3.0, 15.0),
    "claude-sonnet-4-5":        (3.0, 15.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-haiku-4-5":          (1.0, 5.0),
}
_DEFAULT_RATE = (3.0, 15.0)

# Approximate per-call token footprint. Measured empirically from the
# existing analyst/synthesis prompts. Keep conservative — overestimates
# cost rather than under so the dashboard never surprises operators.
_CALL_PROFILES: dict[str, tuple[int, int]] = {
    # kind → (input_tokens, output_tokens)
    "analyst":   (1500, 300),
    "synthesis": (3000, 600),
}


@dataclass
class CostEntry:
    kind: str           # ``analyst`` | ``synthesis`` | custom
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunCostTracker:
    """Accumulates LLM calls per run and reports totals.

    Call counts are per-kind and per-model so the dashboard can show
    "7 analyst calls (Sonnet) + 1 synthesis call (Sonnet) = $0.085".
    """

    mode: RunMode = RunMode.FULL
    entries: list[CostEntry] = field(default_factory=list)

    def record(
        self, *, kind: str, model: str,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
    ) -> CostEntry:
        """Record one LLM call against the tracker. Returns the created entry."""
        profile_in, profile_out = _CALL_PROFILES.get(kind, (1000, 200))
        input_tokens = input_tokens if input_tokens is not None else profile_in
        output_tokens = output_tokens if output_tokens is not None else profile_out
        cost = estimate_cost_usd(model, input_tokens, output_tokens)
        entry = CostEntry(
            kind=kind,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
        )
        self.entries.append(entry)
        return entry

    # ── Aggregations ────────────────────────────────────────────────────

    @property
    def total_calls(self) -> int:
        return len(self.entries)

    @property
    def total_cost_usd(self) -> float:
        return round(sum(e.cost_usd for e in self.entries), 5)

    def calls_by_kind(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.entries:
            out[e.kind] = out.get(e.kind, 0) + 1
        return out

    def calls_by_model(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.entries:
            out[e.model] = out.get(e.model, 0) + 1
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "total_calls": self.total_calls,
            "total_cost_usd": self.total_cost_usd,
            "calls_by_kind": self.calls_by_kind(),
            "calls_by_model": self.calls_by_model(),
            "entries": [e.to_dict() for e in self.entries],
        }


def estimate_cost_usd(
    model: str, input_tokens: int, output_tokens: int,
) -> float:
    """Return an approximate USD cost for one LLM call."""
    input_rate, output_rate = _TOKEN_RATES.get(model, _DEFAULT_RATE)
    cost = (input_tokens / 1_000_000) * input_rate
    cost += (output_tokens / 1_000_000) * output_rate
    return round(cost, 6)


def projected_full_mode_cost(
    tracker: RunCostTracker, *, ticker_count: int, model: str,
) -> float:
    """Return what FULL-mode would have cost (analyst × N + synthesis × 1).

    Used by the DEGRADED-mode savings calculation so the dashboard can
    show "saved $0.07 vs full-mode projection". Pure estimate — the real
    FULL-mode cost is tracker.total_cost_usd when mode=FULL.
    """
    analyst = estimate_cost_usd(model, *_CALL_PROFILES["analyst"]) * ticker_count
    synthesis = estimate_cost_usd(model, *_CALL_PROFILES["synthesis"])
    return round(analyst + synthesis, 6)
