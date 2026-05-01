"""Intel v2 — thesis score schema.

Output models for the deterministic thesis score engine.  Pure data
classes — no IO, no LLM dependency, no external imports.

All numeric bounds are enforced by the engine, not here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ScoreStatus(str, Enum):
    """Overall status of a ScoreCard."""
    READY = "READY"
    PARTIAL = "PARTIAL"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class ConvictionBand(str, Enum):
    """Blended conviction band derived from the weighted subscore blend."""
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass
class SubScore:
    """Score for a single dimension of the investment thesis.

    Attributes:
        score:          0–100 dimensional score (meaningful only when published=True).
        data_quality:   0–1 fraction of defined inputs that were present.
        inputs_used:    Input field names that were present and scored.
        inputs_missing: Input field names that were absent.
        published:      False when data_quality < MIN_SUBSCORE_QUALITY;
                        consumers must ignore ``score`` when False.
    """
    score: float
    data_quality: float
    inputs_used: list[str]
    inputs_missing: list[str]
    published: bool


@dataclass
class ScoreCard:
    """Full deterministic thesis scorecard for one ticker.

    Attributes:
        ticker:               Ticker symbol.
        status:               READY / PARTIAL / INSUFFICIENT_DATA.
        quality:              Business quality subscore.
        valuation:            Valuation attractiveness subscore (higher = cheaper).
        growth:               Growth momentum subscore.
        risk:                 Safety/risk subscore (higher = safer).
        momentum:             Price/trend momentum subscore.
        conviction_score:     Blended 0–100 score; None when data quality is too weak.
        conviction_band:      HIGH / MEDIUM / LOW / INSUFFICIENT_DATA.
        blended_data_quality: Weighted average data quality across published subscores.
        inputs_used:          Union of all subscore inputs_used lists.
        inputs_missing:       Union of all subscore inputs_missing lists.
        score_version:        Schema version for forward compatibility.
    """
    ticker: str
    status: ScoreStatus
    quality: SubScore
    valuation: SubScore
    growth: SubScore
    risk: SubScore
    momentum: SubScore
    conviction_score: Optional[float]
    conviction_band: ConvictionBand
    blended_data_quality: float
    inputs_used: list[str]
    inputs_missing: list[str]
    score_version: str = "v1"
