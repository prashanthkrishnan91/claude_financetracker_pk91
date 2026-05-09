"""Phase 14C.1 — EPS payload shape extractor for FY EPS earnings yield.

Pure, deterministic, read-only helper that extracts a computable FY EPS
observation from a stored research_artifact_facts structured_payload.

Supported payload shapes (from existing SEC adapter / parser code paths):

  Shape A — explicit FY period + fiscal year (most common):
      fiscal_period == "FY"  AND  fiscal_year present  AND  value present

  Shape B — explicit FY period, fiscal year absent:
      fiscal_period == "FY"  AND  fiscal_year absent
      → ordering_year derived from filed date (always present per parser spec)

  Shape C — form 10-K, fiscal_period field absent/None:
      fiscal_period absent/None  AND  form == "10-K"  AND  filed present
      → FY-equivalent because 10-K is the annual report form
      → ordering_year from fiscal_year if present, else from filed date

All shapes require:
  - claim == "sec_companyfact_observed"  (checked by caller before call)
  - fact_kind == "metric_observation"    (checked by caller before call)
  - tag in ("EarningsPerShareDiluted", "EarningsPerShareBasic")
  - has_source == True (source_id present — source-linked)
  - numeric EPS value (int or float, from the "value" key)
  - FY signal: fiscal_period == "FY"  OR  (fiscal_period absent AND form == "10-K")

Shapes explicitly NOT supported (skip with SKIP_NOT_FY):
  - fiscal_period in ("Q1", "Q2", "Q3") regardless of form
  - fiscal_period absent AND form == "10-Q"

Shapes that are skipped with SKIP_MISSING_YEAR (cannot order without year):
  - Any FY-period fact where fiscal_year is absent AND filed date is absent
    or unparseable as a 4-digit year

Architecture invariants (non-negotiable):
  - No IO, no DB, no provider, no LLM.
  - No decision authority.
  - Never fabricates EPS values.
  - Never infers EPS from non-EPS tags.
  - Never returns raw EPS values in skip paths.
  - Return type is always EpsExtractionResult — never raises.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

EPS_EXTRACTION_SCHEMA_VERSION: str = "eps_payload_extractor_v1"

_EPS_TAGS: frozenset[str] = frozenset({
    "EarningsPerShareDiluted",
    "EarningsPerShareBasic",
})

# ── Skip reason constants (used by caller to accumulate diagnostics) ──────────
SKIP_WRONG_TAG: str = "wrong_tag"
SKIP_NOT_SOURCE_LINKED: str = "not_source_linked"
SKIP_MISSING_VALUE: str = "missing_numeric_value"
SKIP_NOT_FY: str = "not_fy_period"
SKIP_MISSING_YEAR: str = "missing_fiscal_year"

# FY-period source labels (internal — never surfaced per-ticker).
_FY_SOURCE_EXPLICIT: str = "fiscal_period_fy"
_FY_SOURCE_FORM_10K: str = "form_10k"

# Year-ordering source labels (internal — never surfaced per-ticker).
_YEAR_SOURCE_FISCAL_YEAR: str = "fiscal_year"
_YEAR_SOURCE_FILED: str = "filed_year"


@dataclass(frozen=True)
class EpsExtractionResult:
    """Extraction result for one structured_payload EPS observation.

    Either ``skip_reason`` is non-empty (observation not computable) or
    ``tag``, ``ordering_year``, and ``eps_value`` are all meaningfully set.

    ``eps_value`` is only consumed by the router to populate
    ``EarningsYieldInputRecord``; it is never exposed in the API response.
    """

    tag: str = ""
    ordering_year: int = 0
    eps_value: float = 0.0
    fy_source: str = ""
    year_source: str = ""
    skip_reason: str = ""


def extract_fy_eps_observation_from_payload(
    structured_payload: dict[str, Any],
    *,
    has_source: bool,
) -> EpsExtractionResult:
    """Extract a computable FY EPS observation from a structured_payload dict.

    Args:
        structured_payload: The ``structured_payload`` dict from a
            ``research_artifact_facts`` row.  Caller must have already
            verified ``fact_kind == "metric_observation"`` and
            ``claim == "sec_companyfact_observed"``.
        has_source: True when the row's ``source_id`` column is non-empty.

    Returns:
        EpsExtractionResult — always.  Never raises.
    """
    try:
        return _extract(structured_payload, has_source=has_source)
    except Exception:  # noqa: BLE001
        return EpsExtractionResult(skip_reason=SKIP_MISSING_VALUE)


def _extract(
    sp: dict[str, Any],
    *,
    has_source: bool,
) -> EpsExtractionResult:
    # ── Tag validation ────────────────────────────────────────────────────────
    tag = str(sp.get("tag") or "")
    if tag not in _EPS_TAGS:
        return EpsExtractionResult(skip_reason=SKIP_WRONG_TAG)

    # ── Source-link requirement ───────────────────────────────────────────────
    if not has_source:
        return EpsExtractionResult(skip_reason=SKIP_NOT_SOURCE_LINKED)

    # ── Numeric value ─────────────────────────────────────────────────────────
    val_raw = sp.get("value")
    if val_raw is None:
        return EpsExtractionResult(skip_reason=SKIP_MISSING_VALUE)
    try:
        eps_value = float(val_raw)
    except (TypeError, ValueError):
        return EpsExtractionResult(skip_reason=SKIP_MISSING_VALUE)

    # ── FY period detection ───────────────────────────────────────────────────
    # Shape A/B: explicit fiscal_period == "FY"
    # Shape C:   fiscal_period absent/None AND form == "10-K"
    fp_raw = sp.get("fiscal_period")
    form_raw = str(sp.get("form") or "").upper().strip()

    if fp_raw is not None and str(fp_raw).upper().strip() == "FY":
        fy_source = _FY_SOURCE_EXPLICIT
    elif fp_raw is None and form_raw == "10-K":
        fy_source = _FY_SOURCE_FORM_10K
    else:
        return EpsExtractionResult(skip_reason=SKIP_NOT_FY)

    # ── Fiscal year ordering ──────────────────────────────────────────────────
    # Primary:  fiscal_year field (int).
    # Fallback: leading 4 digits of filed date (always present per parser spec).
    ordering_year: int | None = None
    year_source: str = ""

    fy_raw = sp.get("fiscal_year")
    if fy_raw is not None:
        try:
            ordering_year = int(fy_raw)
            year_source = _YEAR_SOURCE_FISCAL_YEAR
        except (TypeError, ValueError):
            ordering_year = None

    if ordering_year is None:
        filed_raw = str(sp.get("filed") or "")
        if len(filed_raw) >= 4:
            try:
                ordering_year = int(filed_raw[:4])
                year_source = _YEAR_SOURCE_FILED
            except (TypeError, ValueError):
                pass

    if ordering_year is None:
        return EpsExtractionResult(skip_reason=SKIP_MISSING_YEAR)

    return EpsExtractionResult(
        tag=tag,
        ordering_year=ordering_year,
        eps_value=eps_value,
        fy_source=fy_source,
        year_source=year_source,
        skip_reason="",
    )
