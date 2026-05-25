"""Stage 9E.1 — Equity Numeric Valuation Input Adapter v1.

Pure, no-IO adapter. Derives ticker-level numeric valuation input readiness
for one equity holding from a Stage 9D canonical equity dataset row and an
optional ticker-level price signal.

This module does NOT produce raw EPS values, raw prices, raw P/E ratios,
fair values, price targets, or XBRL metric names in its output. It produces
safe readiness labels (AVAILABLE/PARTIAL/MISSING/STALE), provenance labels,
and period identities for use by the Stage 9E valuation evidence module.

Architecture contracts (non-negotiable):
  - Pure function. No IO, no DB, no LLM, no external calls.
  - Never imports or calls decide().
  - Never serializes raw numeric values (EPS, price, P/E, yield, XBRL keys).
  - metric_family is an abstract label (NET_INCOME / EPS / ...), never a raw name.
  - period_identity exposes only safe metadata (fiscal_year, period, date, unit, form).
  - numeric_inputs_ready=True only when ALL of the following hold:
      canonical_equity_dataset_safe=True
      price_input.numeric_price_confirmed=True
        (market_price_usd present in snapshot — per-share field, not just market_value_certified_at)
      price_input.freshness_label in (FRESH, AGING)
      numeric_earnings_confirmed=True
        (EvidenceSectionRecord.latest_period_identity is not None
         AND EvidenceSectionRecord.source_artifact_id is not None,
         proving actual fact records were loaded — section status alone is not sufficient)
  - market_value_certified_at alone does NOT confirm per-share price readiness.
  - canonical section status alone does NOT confirm numeric earnings readiness.
  - ETF/crypto: numeric_inputs_ready=False, all inputs MISSING.
  - valuation_interpretation_band is NOT set here — that is the responsibility
    of the Stage 9E valuation evidence module (and requires external thresholds).
  - safe_for_decision is permanently False.
  - synthesis_ready is permanently False.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from .canonical_equity_dataset_v1 import (
    SECTION_CASH_FLOW_FCF,
    SECTION_NET_INCOME_EPS,
    SECTION_REVENUE,
    SECTION_STATUS_AVAILABLE,
    SECTION_STATUS_PARTIAL,
    CanonicalEquityDatasetRow,
    EvidenceSectionRecord,
)
from .research_evidence_decision_input_adapter_v1 import (
    INSTRUMENT_CATEGORY_EQUITY,
)

MODEL_VERSION = "equity_numeric_valuation_inputs.v1"

# ── Input status values ────────────────────────────────────────────────────────

INPUT_STATUS_AVAILABLE = "AVAILABLE"
INPUT_STATUS_PARTIAL = "PARTIAL"
INPUT_STATUS_MISSING = "MISSING"
INPUT_STATUS_STALE = "STALE"

_USABLE_INPUT_STATUSES = frozenset({INPUT_STATUS_AVAILABLE, INPUT_STATUS_PARTIAL})

# ── Freshness labels ───────────────────────────────────────────────────────────

FRESHNESS_FRESH = "FRESH"
FRESHNESS_AGING = "AGING"
FRESHNESS_STALE = "STALE"
FRESHNESS_UNKNOWN = "UNKNOWN"

_FRESH_ENOUGH_LABELS = frozenset({FRESHNESS_FRESH, FRESHNESS_AGING})

# ── Price source types ─────────────────────────────────────────────────────────

PRICE_SOURCE_SNAPSHOT_CERTIFIED = "portfolio_snapshot_ticker_certified"
PRICE_SOURCE_SNAPSHOT_CARRIED = "portfolio_snapshot_ticker_carried"
PRICE_SOURCE_SNAPSHOT_PROXY = "portfolio_snapshot_portfolio_level_proxy"
PRICE_SOURCE_NONE = "none"

# ── Metric family labels ───────────────────────────────────────────────────────

METRIC_FAMILY_NET_INCOME = "NET_INCOME"
METRIC_FAMILY_EPS = "EPS"
METRIC_FAMILY_OPERATING_CASH_FLOW = "OPERATING_CASH_FLOW"
METRIC_FAMILY_FCF_DERIVABLE = "FCF_DERIVABLE"
METRIC_FAMILY_UNKNOWN = "UNKNOWN"

# Unit strings that indicate an EPS (per-share) metric rather than net income.
_EPS_UNIT_INDICATORS = frozenset({
    "usd/shares", "per share", "per_share",
})


# ── Ticker price signal ────────────────────────────────────────────────────────


@dataclass
class TickerPriceSignal:
    """Ticker-level price availability signal derived from portfolio snapshot.

    Carries only safe metadata — no raw prices, no market values, no financial
    ratios. The signal is derived by the forensics layer from portfolio_snapshots
    and passed into this module.

    ticker_level_confirmed=True only when market_value_certified_at is present
    (i.e., price was explicitly refreshed by Watchtower, not just carried forward).

    numeric_price_confirmed=True only when market_price_usd (the per-share price
    field) is also present in the snapshot position. market_value_certified_at
    alone does NOT confirm per-share price.
    """

    ticker: str
    source_type: str          # one of PRICE_SOURCE_* constants
    freshness_label: str      # FRESH | AGING | STALE | UNKNOWN
    ticker_level_confirmed: bool  # True only when market_value_certified_at present
    numeric_price_confirmed: bool = False  # True only when market_price_usd also present

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "source_type": self.source_type,
            "freshness_label": self.freshness_label,
            "ticker_level_confirmed": self.ticker_level_confirmed,
        }


# ── Input sub-models ───────────────────────────────────────────────────────────


@dataclass
class PriceInput:
    """Price availability contract for one ticker.

    status=AVAILABLE: ticker-level price confirmed fresh, certified by Watchtower.
    status=PARTIAL: ticker appears in snapshot but price was carried forward (not certified).
    status=MISSING: no snapshot or ticker not in snapshot.
    status=STALE: snapshot exists but is too old to be considered fresh enough.

    numeric_price_confirmed=True only when the per-share price field (market_price_usd)
    is explicitly present in the snapshot position. ticker_level_confirmed=True alone
    (market_value_certified_at present) does NOT imply numeric_price_confirmed=True.
    """

    status: str               # AVAILABLE | PARTIAL | MISSING | STALE
    source_type: str          # PRICE_SOURCE_* constant
    freshness_label: str      # FRESH | AGING | STALE | UNKNOWN
    ticker_level_confirmed: bool  # True only when market_value_certified_at confirmed
    numeric_price_confirmed: bool = False  # True only when market_price_usd present

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "source_type": self.source_type,
            "freshness_label": self.freshness_label,
            "ticker_level_confirmed": self.ticker_level_confirmed,
            "numeric_price_confirmed": self.numeric_price_confirmed,
        }


@dataclass
class EarningsInput:
    """Earnings/EPS availability contract for one ticker.

    Derived from the canonical equity dataset's SECTION_NET_INCOME_EPS section.
    Raw EPS values are NEVER stored or serialized here.

    metric_family is a safe abstract label (NET_INCOME or EPS), derived from
    the period identity's unit field. Never a raw XBRL metric name.
    """

    status: str               # AVAILABLE | PARTIAL | MISSING | STALE
    metric_family: str        # NET_INCOME | EPS | UNKNOWN
    period_identity: Optional[dict]   # safe PeriodIdentity dict from canonical dataset
    source_artifact_id: Optional[str]  # UUID reference to SEC artifact

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "metric_family": self.metric_family,
            "period_identity": self.period_identity,
            "source_artifact_id": self.source_artifact_id,
        }


@dataclass
class CashFlowInput:
    """Cash flow/FCF availability contract for one ticker.

    Derived from the canonical equity dataset's SECTION_CASH_FLOW_FCF section.
    Raw cash flow values are NEVER stored or serialized here.
    """

    status: str               # AVAILABLE | PARTIAL | MISSING | STALE
    metric_family: str        # OPERATING_CASH_FLOW | FCF_DERIVABLE | UNKNOWN
    period_identity: Optional[dict]   # safe PeriodIdentity dict from canonical dataset
    source_artifact_id: Optional[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "metric_family": self.metric_family,
            "period_identity": self.period_identity,
            "source_artifact_id": self.source_artifact_id,
        }


@dataclass
class GrowthInput:
    """Growth context input availability contract for one ticker.

    Derived from canonical equity dataset section statuses only.
    No raw values — only section names that are AVAILABLE or PARTIAL.
    """

    status: str           # AVAILABLE | PARTIAL | MISSING
    based_on_sections: list  # list[str] — section names that contribute (AVAILABLE/PARTIAL)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "based_on_sections": list(self.based_on_sections),
        }


# ── Main contract ──────────────────────────────────────────────────────────────


@dataclass
class EquityNumericValuationInputs:
    """Numeric valuation input readiness contract for one equity ticker.

    Safe for diagnostics and downstream adapters. No raw EPS values, no raw
    prices, no P/E ratios, no fair values, no price targets, no XBRL names.

    Readiness fields (coarser → finer):
      valuation_input_scaffold_present: canonical dataset safe + any price signal exists
      ticker_price_metadata_present: market_value_certified_at present for this ticker
      numeric_price_confirmed: market_price_usd (per-share) explicitly present
      numeric_earnings_confirmed: actual fact records loaded (latest_period_identity non-None
        AND source_artifact_id non-None); section status alone is NOT sufficient

    numeric_inputs_ready=True only when ALL hold:
      - canonical_equity_dataset_safe=True
      - numeric_price_confirmed=True (market_price_usd present in snapshot position)
      - price_input.freshness_label in (FRESH, AGING)
      - numeric_earnings_confirmed=True

    safe_for_decision and synthesis_ready are permanently False.
    valuation_interpretation_band is not set here — belongs to the valuation
    evidence module (Stage 9E) which owns threshold application.
    """

    ticker: str
    asset_type: str
    input_version: str   # MODEL_VERSION constant
    generated_at: str

    price_input: PriceInput
    earnings_input: EarningsInput
    cash_flow_input: CashFlowInput
    growth_input: GrowthInput

    missing_reasons: dict   # dict[str, str] — explicit blockers per sub-section

    # Readiness scaffold (coarser → finer).
    valuation_input_scaffold_present: bool  # canonical safe + at least a price signal
    ticker_price_metadata_present: bool     # market_value_certified_at present
    numeric_price_confirmed: bool           # market_price_usd (per-share) present
    numeric_earnings_confirmed: bool        # fact records loaded with period_identity

    numeric_inputs_ready: bool  # True only when numeric price AND earnings both confirmed

    # Immutable safety gates.
    safe_for_decision: bool = False   # always False
    synthesis_ready: bool = False     # always False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "asset_type": self.asset_type,
            "input_version": self.input_version,
            "generated_at": self.generated_at,
            "price_input": self.price_input.to_dict(),
            "earnings_input": self.earnings_input.to_dict(),
            "cash_flow_input": self.cash_flow_input.to_dict(),
            "growth_input": self.growth_input.to_dict(),
            "missing_reasons": dict(self.missing_reasons),
            "valuation_input_scaffold_present": self.valuation_input_scaffold_present,
            "ticker_price_metadata_present": self.ticker_price_metadata_present,
            "numeric_price_confirmed": self.numeric_price_confirmed,
            "numeric_earnings_confirmed": self.numeric_earnings_confirmed,
            "numeric_inputs_ready": self.numeric_inputs_ready,
            "safe_for_decision": self.safe_for_decision,
            "synthesis_ready": self.synthesis_ready,
        }


# ── Public API ─────────────────────────────────────────────────────────────────


def build_equity_numeric_valuation_inputs(
    *,
    canonical_row: CanonicalEquityDatasetRow,
    ticker_price_signal: Optional[TickerPriceSignal] = None,
) -> EquityNumericValuationInputs:
    """Build the numeric valuation input readiness contract for one equity ticker.

    Args:
        canonical_row: Stage 9D CanonicalEquityDatasetRow for this ticker.
        ticker_price_signal: Optional ticker-level price signal from the forensics
            layer (derived from portfolio_snapshots positions_data). When None,
            price availability is MISSING (no ticker-level confirmation).

    Returns:
        EquityNumericValuationInputs — always non-None, never raises.
        numeric_inputs_ready=False when inputs are incomplete/stale/ambiguous.
        safe_for_decision=False always. synthesis_ready=False always.
        ETF/crypto: all inputs MISSING, numeric_inputs_ready=False.
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    if canonical_row.asset_type != INSTRUMENT_CATEGORY_EQUITY:
        return _build_not_applicable_inputs(canonical_row, now_iso)

    missing_reasons: dict[str, str] = {}
    sections = canonical_row.operating_trends.sections
    canonical_safe = canonical_row.safe_for_equity_dataset

    price_input = _derive_price_input(
        ticker=canonical_row.ticker,
        ticker_price_signal=ticker_price_signal,
        canonical_safe=canonical_safe,
        missing_reasons=missing_reasons,
    )

    earnings_input = _derive_earnings_input(
        sections=sections,
        canonical_safe=canonical_safe,
        missing_reasons=missing_reasons,
    )

    cash_flow_input = _derive_cash_flow_input(
        sections=sections,
        canonical_safe=canonical_safe,
        missing_reasons=missing_reasons,
    )

    growth_input = _derive_growth_input(
        sections=sections,
        canonical_safe=canonical_safe,
        missing_reasons=missing_reasons,
    )

    # numeric_earnings_confirmed: requires actual fact records, not just section status.
    # EvidenceSectionRecord.latest_period_identity is None in the metadata-only fallback path.
    earnings_section = sections.get(SECTION_NET_INCOME_EPS) if canonical_safe else None
    numeric_earnings_confirmed = bool(
        earnings_section is not None
        and earnings_section.latest_period_identity is not None
        and earnings_section.source_artifact_id is not None
    )

    valuation_input_scaffold_present = bool(canonical_safe and ticker_price_signal is not None)
    ticker_price_metadata_present = price_input.ticker_level_confirmed
    numeric_price_confirmed = price_input.numeric_price_confirmed

    numeric_inputs_ready = _compute_numeric_inputs_ready(
        canonical_safe=canonical_safe,
        price_input=price_input,
        numeric_earnings_confirmed=numeric_earnings_confirmed,
        missing_reasons=missing_reasons,
    )

    return EquityNumericValuationInputs(
        ticker=canonical_row.ticker,
        asset_type=canonical_row.asset_type,
        input_version=MODEL_VERSION,
        generated_at=now_iso,
        price_input=price_input,
        earnings_input=earnings_input,
        cash_flow_input=cash_flow_input,
        growth_input=growth_input,
        missing_reasons=missing_reasons,
        valuation_input_scaffold_present=valuation_input_scaffold_present,
        ticker_price_metadata_present=ticker_price_metadata_present,
        numeric_price_confirmed=numeric_price_confirmed,
        numeric_earnings_confirmed=numeric_earnings_confirmed,
        numeric_inputs_ready=numeric_inputs_ready,
        safe_for_decision=False,
        synthesis_ready=False,
    )


# ── Internal helpers ───────────────────────────────────────────────────────────


def _derive_price_input(
    *,
    ticker: str,
    ticker_price_signal: Optional[TickerPriceSignal],
    canonical_safe: bool,
    missing_reasons: dict,
) -> PriceInput:
    """Derive price input status from a ticker-level price signal.

    numeric_price_confirmed is propagated from the signal's numeric_price_confirmed flag,
    which is True only when market_price_usd (per-share) was present in the snapshot
    position. market_value_certified_at alone does NOT make numeric_price_confirmed=True.
    """
    if ticker_price_signal is None or not ticker_price_signal.ticker_level_confirmed:
        if ticker_price_signal is not None:
            # Signal exists but not certified (carried forward price).
            if ticker_price_signal.source_type == PRICE_SOURCE_SNAPSHOT_CARRIED:
                missing_reasons["price_input"] = (
                    "Ticker-level price is carried forward from a prior snapshot "
                    "(market_value_certified_at not set). Price is not certified fresh. "
                    "Run Watchtower to refresh prices and set market_value_certified_at."
                )
                return PriceInput(
                    status=INPUT_STATUS_PARTIAL,
                    source_type=ticker_price_signal.source_type,
                    freshness_label=ticker_price_signal.freshness_label,
                    ticker_level_confirmed=False,
                    numeric_price_confirmed=False,
                )
        # No signal at all.
        missing_reasons["price_input"] = (
            "Ticker-level price is not confirmed. No portfolio snapshot or ticker "
            "not found in latest snapshot. Run Watchtower to populate portfolio prices."
        )
        return PriceInput(
            status=INPUT_STATUS_MISSING,
            source_type=PRICE_SOURCE_NONE,
            freshness_label=FRESHNESS_UNKNOWN,
            ticker_level_confirmed=False,
            numeric_price_confirmed=False,
        )

    # ticker_level_confirmed=True below this point.
    freshness = ticker_price_signal.freshness_label
    numeric_price_confirmed = ticker_price_signal.numeric_price_confirmed

    if freshness == FRESHNESS_STALE:
        missing_reasons["price_input"] = (
            "Ticker-level price exists but is stale (snapshot too old). "
            "Run Watchtower to refresh and recertify."
        )
        return PriceInput(
            status=INPUT_STATUS_STALE,
            source_type=ticker_price_signal.source_type,
            freshness_label=freshness,
            ticker_level_confirmed=True,
            numeric_price_confirmed=False,
        )

    if not numeric_price_confirmed:
        # market_value_certified_at is present but market_price_usd is absent.
        missing_reasons["price_input"] = (
            "Market value certification exists but per-share price input is not confirmed. "
            "market_price_usd is absent from the portfolio snapshot position."
        )

    return PriceInput(
        status=INPUT_STATUS_AVAILABLE,
        source_type=ticker_price_signal.source_type,
        freshness_label=freshness,
        ticker_level_confirmed=True,
        numeric_price_confirmed=numeric_price_confirmed,
    )


def _derive_earnings_input(
    *,
    sections: dict,
    canonical_safe: bool,
    missing_reasons: dict,
) -> EarningsInput:
    """Derive earnings/EPS input from SECTION_NET_INCOME_EPS section record."""
    if not canonical_safe:
        missing_reasons["earnings_input"] = (
            "Canonical equity dataset is not safe — earnings/EPS input cannot be derived."
        )
        return EarningsInput(
            status=INPUT_STATUS_MISSING,
            metric_family=METRIC_FAMILY_UNKNOWN,
            period_identity=None,
            source_artifact_id=None,
        )

    section = sections.get(SECTION_NET_INCOME_EPS)
    if section is None:
        missing_reasons["earnings_input"] = (
            "Net income/EPS section not found in canonical equity dataset."
        )
        return EarningsInput(
            status=INPUT_STATUS_MISSING,
            metric_family=METRIC_FAMILY_UNKNOWN,
            period_identity=None,
            source_artifact_id=None,
        )

    if section.status not in _USABLE_INPUT_STATUSES:
        missing_reasons["earnings_input"] = (
            f"Net income/EPS section status is {section.status or 'missing'} "
            "in the canonical equity dataset. A minimum of PARTIAL is required for "
            "numeric earnings input."
        )
        return EarningsInput(
            status=INPUT_STATUS_MISSING,
            metric_family=METRIC_FAMILY_UNKNOWN,
            period_identity=None,
            source_artifact_id=None,
        )

    metric_family = _infer_earnings_metric_family(section)
    period_id = (
        section.latest_period_identity.to_dict()
        if section.latest_period_identity else None
    )

    return EarningsInput(
        status=(
            INPUT_STATUS_AVAILABLE
            if section.status == SECTION_STATUS_AVAILABLE
            else INPUT_STATUS_PARTIAL
        ),
        metric_family=metric_family,
        period_identity=period_id,
        source_artifact_id=section.source_artifact_id,
    )


def _derive_cash_flow_input(
    *,
    sections: dict,
    canonical_safe: bool,
    missing_reasons: dict,
) -> CashFlowInput:
    """Derive cash flow input from SECTION_CASH_FLOW_FCF section record."""
    if not canonical_safe:
        missing_reasons["cash_flow_input"] = (
            "Canonical equity dataset is not safe — cash flow input cannot be derived."
        )
        return CashFlowInput(
            status=INPUT_STATUS_MISSING,
            metric_family=METRIC_FAMILY_UNKNOWN,
            period_identity=None,
            source_artifact_id=None,
        )

    section = sections.get(SECTION_CASH_FLOW_FCF)
    if section is None:
        missing_reasons["cash_flow_input"] = (
            "Cash flow/FCF section not found in canonical equity dataset."
        )
        return CashFlowInput(
            status=INPUT_STATUS_MISSING,
            metric_family=METRIC_FAMILY_UNKNOWN,
            period_identity=None,
            source_artifact_id=None,
        )

    if section.status not in _USABLE_INPUT_STATUSES:
        missing_reasons["cash_flow_input"] = (
            f"Cash flow/FCF section status is {section.status or 'missing'} "
            "in the canonical equity dataset. PARTIAL or AVAILABLE required."
        )
        return CashFlowInput(
            status=INPUT_STATUS_MISSING,
            metric_family=METRIC_FAMILY_UNKNOWN,
            period_identity=None,
            source_artifact_id=None,
        )

    # AVAILABLE (≥2 annual obs) → operating cash flow confirmed; PARTIAL → FCF derivable.
    metric_family = (
        METRIC_FAMILY_OPERATING_CASH_FLOW
        if section.status == SECTION_STATUS_AVAILABLE
        else METRIC_FAMILY_FCF_DERIVABLE
    )
    period_id = (
        section.latest_period_identity.to_dict()
        if section.latest_period_identity else None
    )

    return CashFlowInput(
        status=(
            INPUT_STATUS_AVAILABLE
            if section.status == SECTION_STATUS_AVAILABLE
            else INPUT_STATUS_PARTIAL
        ),
        metric_family=metric_family,
        period_identity=period_id,
        source_artifact_id=section.source_artifact_id,
    )


def _derive_growth_input(
    *,
    sections: dict,
    canonical_safe: bool,
    missing_reasons: dict,
) -> GrowthInput:
    """Derive growth input from revenue + net income/EPS section statuses.

    Only section names are used — raw values are never inspected here.
    based_on_sections lists which sections are AVAILABLE or PARTIAL.
    """
    if not canonical_safe:
        missing_reasons["growth_input"] = (
            "Canonical equity dataset is not safe — growth input cannot be derived."
        )
        return GrowthInput(status=INPUT_STATUS_MISSING, based_on_sections=[])

    contributing_sections: list[str] = []
    revenue_section = sections.get(SECTION_REVENUE)
    eps_section = sections.get(SECTION_NET_INCOME_EPS)

    if revenue_section and revenue_section.status in _USABLE_INPUT_STATUSES:
        contributing_sections.append(SECTION_REVENUE)
    if eps_section and eps_section.status in _USABLE_INPUT_STATUSES:
        contributing_sections.append(SECTION_NET_INCOME_EPS)

    if not contributing_sections:
        missing_reasons["growth_input"] = (
            "Neither revenue nor net income/EPS sections are available in the "
            "canonical equity dataset. Growth context cannot be derived without "
            "at least one AVAILABLE or PARTIAL section."
        )
        return GrowthInput(status=INPUT_STATUS_MISSING, based_on_sections=[])

    if len(contributing_sections) == 2:
        rev_status = (revenue_section.status if revenue_section else None)
        eps_status = (eps_section.status if eps_section else None)
        if (
            rev_status == SECTION_STATUS_AVAILABLE
            and eps_status == SECTION_STATUS_AVAILABLE
        ):
            return GrowthInput(
                status=INPUT_STATUS_AVAILABLE,
                based_on_sections=contributing_sections,
            )
        return GrowthInput(
            status=INPUT_STATUS_PARTIAL,
            based_on_sections=contributing_sections,
        )

    # Only one section is AVAILABLE/PARTIAL.
    return GrowthInput(
        status=INPUT_STATUS_PARTIAL,
        based_on_sections=contributing_sections,
    )


def _compute_numeric_inputs_ready(
    *,
    canonical_safe: bool,
    price_input: PriceInput,
    numeric_earnings_confirmed: bool,
    missing_reasons: dict,
) -> bool:
    """Compute whether all minimum numeric valuation inputs are confirmed.

    Requires:
      - canonical_equity_dataset_safe=True
      - price_input.numeric_price_confirmed=True (market_price_usd present in snapshot)
      - price_input.freshness_label in (FRESH, AGING)
      - numeric_earnings_confirmed=True (actual fact records loaded, not just section status)

    market_value_certified_at alone does NOT satisfy the price requirement.
    Section status alone does NOT satisfy the earnings requirement.
    Cash flow and growth inputs are supplemental and do NOT gate numeric_inputs_ready.
    """
    if not canonical_safe:
        missing_reasons["numeric_inputs_ready"] = (
            "Canonical equity dataset is not safe. "
            "Fix SEC company facts artifact first (see sec_companyfacts diagnostic)."
        )
        return False

    if not price_input.numeric_price_confirmed:
        if price_input.ticker_level_confirmed:
            missing_reasons["numeric_inputs_ready"] = (
                "Market value certification exists but per-share price input (market_price_usd) "
                "is not confirmed in the portfolio snapshot position."
            )
        else:
            missing_reasons["numeric_inputs_ready"] = (
                "Ticker-level price is not confirmed (no certified market_value_certified_at "
                "for this ticker in the latest portfolio snapshot). Run Watchtower price refresh."
            )
        return False

    if price_input.freshness_label not in _FRESH_ENOUGH_LABELS:
        missing_reasons["numeric_inputs_ready"] = (
            f"Ticker-level price exists but freshness is {price_input.freshness_label}. "
            "Price must be FRESH or AGING for numeric valuation input. "
            "Run Watchtower to refresh and recertify."
        )
        return False

    if not numeric_earnings_confirmed:
        missing_reasons["numeric_inputs_ready"] = (
            "Earnings/EPS numeric data is not confirmed. "
            "Section status alone is not sufficient — actual fact records with a confirmed "
            "period identity are required (latest_period_identity must be non-None)."
        )
        return False

    return True


def _infer_earnings_metric_family(section: EvidenceSectionRecord) -> str:
    """Infer earnings metric family from the section's period identity unit.

    EPS metrics use a 'per share' unit (e.g., 'USD/shares').
    Net income metrics use absolute currency units (e.g., 'USD').
    When unit is absent or ambiguous, returns UNKNOWN.

    This is an abstract family label — never a raw XBRL metric name.
    """
    if section.latest_period_identity is None:
        return METRIC_FAMILY_UNKNOWN
    unit = (section.latest_period_identity.unit or "").lower().strip()
    if not unit:
        return METRIC_FAMILY_UNKNOWN
    for indicator in _EPS_UNIT_INDICATORS:
        if indicator in unit:
            return METRIC_FAMILY_EPS
    if "usd" in unit or "$" in unit:
        return METRIC_FAMILY_NET_INCOME
    return METRIC_FAMILY_UNKNOWN


def _build_not_applicable_inputs(
    canonical_row: CanonicalEquityDatasetRow,
    now_iso: str,
) -> EquityNumericValuationInputs:
    """Return a NOT_APPLICABLE inputs row for non-equity tickers."""
    reason = (
        f"Numeric valuation inputs are not applicable for asset type "
        f"'{canonical_row.asset_type}'. ETF and crypto require dedicated provider lanes."
    )
    not_applicable_price = PriceInput(
        status=INPUT_STATUS_MISSING,
        source_type=PRICE_SOURCE_NONE,
        freshness_label=FRESHNESS_UNKNOWN,
        ticker_level_confirmed=False,
    )
    not_applicable_earnings = EarningsInput(
        status=INPUT_STATUS_MISSING,
        metric_family=METRIC_FAMILY_UNKNOWN,
        period_identity=None,
        source_artifact_id=None,
    )
    not_applicable_cf = CashFlowInput(
        status=INPUT_STATUS_MISSING,
        metric_family=METRIC_FAMILY_UNKNOWN,
        period_identity=None,
        source_artifact_id=None,
    )
    not_applicable_growth = GrowthInput(
        status=INPUT_STATUS_MISSING,
        based_on_sections=[],
    )
    return EquityNumericValuationInputs(
        ticker=canonical_row.ticker,
        asset_type=canonical_row.asset_type,
        input_version=MODEL_VERSION,
        generated_at=now_iso,
        price_input=not_applicable_price,
        earnings_input=not_applicable_earnings,
        cash_flow_input=not_applicable_cf,
        growth_input=not_applicable_growth,
        missing_reasons={"all": reason},
        valuation_input_scaffold_present=False,
        ticker_price_metadata_present=False,
        numeric_price_confirmed=False,
        numeric_earnings_confirmed=False,
        numeric_inputs_ready=False,
        safe_for_decision=False,
        synthesis_ready=False,
    )
