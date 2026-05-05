"""V3 Data Truth Contract v1 — typed enums and dataclasses.

Represents truth/freshness/source/completeness for existing Intel signals
before they are trusted by v3 decision logic.

Pure data types only. No IO, no LLM, no DB.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List


class DataTruthStatus(str, Enum):
    """Classification of a signal field's data truth state."""
    PRESENT = "PRESENT"
    MISSING = "MISSING"
    STALE = "STALE"
    WEAK = "WEAK"
    CONFLICTING = "CONFLICTING"
    UNAVAILABLE = "UNAVAILABLE"


class SourceTrustLevel(str, Enum):
    """Trust level for the source of a signal field."""
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


@dataclass
class DataTruthFinding:
    """Truth classification for a single signal field or signal group.

    safe_for_decision=True when the field is usable as v3 decision input.
    PRESENT and WEAK are safe (WEAK carries LOW trust).
    MISSING, STALE, CONFLICTING, and UNAVAILABLE are not safe.
    """
    signal_name: str
    status: DataTruthStatus
    trust_level: SourceTrustLevel
    source_kind: str
    freshness_label: str
    reason_code: str
    safe_for_decision: bool


@dataclass
class AxisTruthSummary:
    """Aggregated truth summary for one decision-relevant signal axis.

    safe_for_decision=True when at least one finding is safe and no
    CONFLICTING or UNAVAILABLE findings are present in this axis.
    """
    axis_name: str
    findings: List[DataTruthFinding] = field(default_factory=list)
    present_count: int = 0
    missing_count: int = 0
    stale_count: int = 0
    weak_count: int = 0
    safe_for_decision: bool = False
    dominant_reason_code: str = ""
