"""V3 Intel decision contracts — typed enums and dataclasses.

Pure data types only. No IO, no LLM, no DB.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ActionV3(str, Enum):
    BUY = "BUY"
    HOLD = "HOLD"
    TRIM = "TRIM"
    SELL = "SELL"


class ConvictionV3(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class AxisBand(str, Enum):
    """Evidence quality / attractiveness axis."""
    THIN = "THIN"
    OK = "OK"
    STRONG = "STRONG"
    SUPPRESSED = "SUPPRESSED"


class PriceBand(str, Enum):
    """Price / valuation context axis."""
    CHEAP = "CHEAP"
    FAIR = "FAIR"
    FULL = "FULL"
    EXPENSIVE = "EXPENSIVE"
    SUPPRESSED = "SUPPRESSED"


class FitBand(str, Enum):
    """Portfolio fit axis."""
    UNDERWEIGHT = "UNDERWEIGHT"
    ON_TARGET = "ON_TARGET"
    OVERWEIGHT = "OVERWEIGHT"
    BREACH = "BREACH"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


class RiskBand(str, Enum):
    """Risk severity axis."""
    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"


@dataclass
class DecisionInputV3:
    """Normalized signal inputs for the v3 decision kernel.

    All fields are optional — missing signals suppress only the relevant axis.
    """
    ticker: str

    evidence_quality: AxisBand = AxisBand.SUPPRESSED
    price_context: PriceBand = PriceBand.SUPPRESSED
    portfolio_fit: FitBand = FitBand.UNKNOWN
    risk_band: RiskBand = RiskBand.UNKNOWN

    # Upstream action signals (normalized to uppercase).
    raw_action: Optional[str] = None
    raw_analyst_action: Optional[str] = None

    # Conviction hint from upstream (HIGH | MEDIUM | LOW).
    upstream_conviction: Optional[str] = None

    # axis → human-readable reason for suppression.
    suppression_reasons: dict = field(default_factory=dict)

    # Audit trail of source signals (no raw metric keys).
    source_signal_summary: dict = field(default_factory=dict)

    # Per-ticker evidence text from analyst verdict (optional).
    # Used by _build_rationale() to produce visible, ticker-specific reason text.
    # Absent when LLM has not run for this ticker or analyst used fallback path.
    primary_driver: Optional[str] = None       # single most important plain-English reason
    risk_flag_text: Optional[str] = None       # biggest single risk that could break thesis
    action_reason: Optional[str] = None        # plain-English explanation of why BUY/HOLD
    analyst_drivers: Optional[list] = field(default_factory=list)
    asset_type_hint: Optional[str] = None      # 'etf' | 'crypto' | 'stock' (default stock)


@dataclass
class DecisionOutputV3:
    """V3 decision kernel output for one ticker.

    Action and conviction are deterministic — no LLM involvement.
    Plain-English fields must not expose raw metric keys.
    """
    ticker: str
    action: ActionV3
    conviction: ConvictionV3
    evidence_quality: AxisBand
    attractiveness: AxisBand
    price_context: PriceBand
    portfolio_fit: FitBand
    risk_band: RiskBand
    blockers: list
    suppression_reasons: dict
    rationale_plain_english: str
    why_now: str
    why_not_now: str
    source_signal_summary: dict
    schema_version: str = "v3.1"
