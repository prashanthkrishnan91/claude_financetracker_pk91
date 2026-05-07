"""Phase 8B — SEC Metric Evidence Snapshot Dry Run.

Purpose:
    Converts the Phase 8A aggregate bucket/tag/unit/form counts into a stable
    per-ticker diagnostic contract.

    Answers:
    - Which tickers have source-linked SEC metric evidence?
    - Which expected evidence buckets are present per ticker?
    - Which expected evidence buckets are missing per ticker?
    - Whether each ticker has minimum SEC evidence readiness for future
      truth-adapter consideration.
    - Why each ticker remains blocked from decision consumption.
    - Whether all outputs remain safe_for_decision=false and
      visible_snapshot_unchanged=true.

    This is a dry run only. It MUST NOT feed normalized metrics into decide(),
    visible snapshots, recommendation cards, portfolio actions, UI copy, or any
    Buy/Hold/Trim/Sell policy.

Architecture invariants (non-negotiable):
    - NEVER imports or calls decide() from decision_policy_v1.
    - NEVER imports IntelV3Service, recommendation_engine, or any frontend path.
    - NEVER writes to any DB table.
    - NEVER feeds normalized evidence into visible decisions.
    - NEVER returns raw metric values, structured_payload, source URLs, excerpts.
    - NEVER returns raw DB rows.
    - NEVER sets safe_for_decision=True.
    - snapshot_safe_for_decision is always False.
    - visible_snapshot_unchanged is always True.
    - Per-ticker forms/units are aggregate counts only — no raw values.
    - Do not compute ratios, growth rates, quality scores, or valuations.
    - Any exception triggers a safe empty result — never propagates to callers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .sec_metric_truth_adapter_dry_run import (
    EXPECTED_BUCKETS,
    SEC_METRIC_BUCKET_MAP,
    SecMetricTruthAdapterDryRunResult,
)

# ── Bucket groups ─────────────────────────────────────────────────────────────
# income_statement_core: requires revenue + at least one of the other three.
# cash_flow_core: requires operating_cash_flow AND capex.
# balance_sheet_core: requires all four.
BUCKET_GROUPS: dict[str, frozenset[str]] = {
    "income_statement_core": frozenset({"revenue", "operating_income", "net_income", "eps"}),
    "cash_flow_core": frozenset({"operating_cash_flow", "capex"}),
    "balance_sheet_core": frozenset({"cash", "assets", "liabilities", "equity"}),
}

_INCOME_STMT_OPTIONAL = frozenset({"operating_income", "net_income", "eps"})
_BALANCE_SHEET_REQUIRED = frozenset({"assets", "liabilities", "equity", "cash"})

FutureAdapterReadiness = Literal[
    "READY_DRY_RUN_ONLY",
    "PARTIAL_DRY_RUN_ONLY",
    "BLOCKED_DRY_RUN_ONLY",
]

# Blocking codes always present on every ticker.
_ALWAYS_BLOCKING: tuple[str, ...] = (
    "decision_consumption_disabled",
    "safe_for_decision_db_lock",
)


@dataclass
class TickerEvidenceSnapshot:
    """Per-ticker evidence snapshot. Aggregate-only — no raw values.

    Forbidden (never present):
        - raw metric values
        - full structured_payload dicts
        - raw source URLs or excerpts
        - raw DB rows
        - Buy/Hold/Trim/Sell signals
        - user-facing UI copy
    """
    source_linked_metric_fact_count: int
    present_buckets: list[str]           # sorted
    missing_buckets: list[str]           # sorted
    present_bucket_groups: list[str]     # sorted
    missing_bucket_groups: list[str]     # sorted
    forms: dict[str, int]                # aggregate counts only
    units: dict[str, int]                # aggregate counts only
    future_adapter_readiness: FutureAdapterReadiness
    blocking_reason_codes: list[str]     # sorted


@dataclass
class SecMetricEvidenceSnapshotDryRunResult:
    """Aggregate-only dry-run result for Phase 8B.

    All fields are safe to log and return via the diagnostics endpoint.

    Invariants:
        snapshot_safe_for_decision is always False.
        visible_snapshot_unchanged is always True.
    """
    snapshot_enabled: bool
    snapshot_safe_for_decision: bool          # always False
    visible_snapshot_unchanged: bool          # always True
    tickers_evaluated_count: int
    tickers_with_any_source_linked_evidence_count: int
    tickers_ready_for_future_adapter_count: int
    tickers_blocked_from_decision_count: int  # always == tickers_evaluated_count
    by_ticker: dict[str, dict]                # ticker → TickerEvidenceSnapshot as dict


def run_sec_metric_evidence_snapshot_dry_run(
    adapter_result: SecMetricTruthAdapterDryRunResult,
    artifact_rows: list[dict],
    facts_by_artifact: dict[str, list[dict]],
) -> SecMetricEvidenceSnapshotDryRunResult:
    """Pure, deterministic, read-only Phase 8B evidence snapshot.

    Takes the Phase 8A adapter result and the same already-fetched artifact/fact
    data (no DB re-query). Returns per-ticker diagnostic snapshots. Never raises.

    Args:
        adapter_result:      Phase 8A dry-run result.
        artifact_rows:       Same artifact rows passed to Phase 8A.
        facts_by_artifact:   Same facts mapping passed to Phase 8A.

    Returns:
        SecMetricEvidenceSnapshotDryRunResult with per-ticker snapshots.
        snapshot_safe_for_decision is always False.
        visible_snapshot_unchanged is always True.
    """
    try:
        return _run(adapter_result, artifact_rows, facts_by_artifact)
    except Exception:  # noqa: BLE001
        return SecMetricEvidenceSnapshotDryRunResult(
            snapshot_enabled=True,
            snapshot_safe_for_decision=False,
            visible_snapshot_unchanged=True,
            tickers_evaluated_count=0,
            tickers_with_any_source_linked_evidence_count=0,
            tickers_ready_for_future_adapter_count=0,
            tickers_blocked_from_decision_count=0,
            by_ticker={},
        )


def _run(
    adapter_result: SecMetricTruthAdapterDryRunResult,
    artifact_rows: list[dict],
    facts_by_artifact: dict[str, list[dict]],
) -> SecMetricEvidenceSnapshotDryRunResult:
    # ── Build per-ticker forms/units by re-iterating in-memory facts ──────────
    ticker_forms: dict[str, dict[str, int]] = {}
    ticker_units: dict[str, dict[str, int]] = {}
    # Collect all unique tickers from artifact_rows (superset of by_ticker).
    all_tickers_set: set[str] = set()

    for artifact_row in artifact_rows:
        aid = str(artifact_row.get("id", ""))
        ticker = str(artifact_row.get("ticker") or "UNKNOWN")
        all_tickers_set.add(ticker)
        artifact_facts = facts_by_artifact.get(aid, [])

        for fact in artifact_facts:
            if str(fact.get("fact_kind") or "") != "metric_observation":
                continue
            sp = fact.get("structured_payload")
            if not isinstance(sp, dict) or sp.get("claim") != "sec_companyfact_observed":
                continue
            source_id = fact.get("source_id")
            if not source_id or not str(source_id).strip():
                continue

            form = str(sp.get("form") or "UNKNOWN")
            unit = str(sp.get("unit") or "UNKNOWN")

            ticker_forms.setdefault(ticker, {})
            ticker_forms[ticker][form] = ticker_forms[ticker].get(form, 0) + 1

            ticker_units.setdefault(ticker, {})
            ticker_units[ticker][unit] = ticker_units[ticker].get(unit, 0) + 1

    # ── Derive per-ticker present/missing buckets from Phase 8A output ────────
    # Use all unique tickers from artifact_rows so that tickers with no
    # source-linked mapped facts still appear as BLOCKED_DRY_RUN_ONLY.
    all_tickers = sorted(all_tickers_set)

    tickers_with_any_source_linked_evidence_count = 0
    tickers_ready_for_future_adapter_count = 0
    by_ticker_out: dict[str, dict] = {}

    for ticker in all_tickers:
        fact_count = adapter_result.by_ticker.get(ticker, 0)

        if fact_count == 0:
            # Ticker present in artifact_rows but has no source-linked mapped facts.
            # All buckets and groups are missing.
            present_b: list[str] = []
            missing_b = sorted(EXPECTED_BUCKETS)
            present_groups: list[str] = []
            missing_groups: list[str] = sorted(BUCKET_GROUPS.keys())
            readiness: FutureAdapterReadiness = "BLOCKED_DRY_RUN_ONLY"
        else:
            missing_b = sorted(adapter_result.missing_buckets_by_ticker.get(ticker, []))
            present_b = sorted(EXPECTED_BUCKETS - set(missing_b))
            present_set = set(present_b)

            # ── Bucket group presence ─────────────────────────────────────────
            present_groups = []
            missing_groups = []
            for group_name, group_buckets in sorted(BUCKET_GROUPS.items()):
                if any(b in present_set for b in group_buckets):
                    present_groups.append(group_name)
                else:
                    missing_groups.append(group_name)

            # ── Readiness ─────────────────────────────────────────────────────
            if _is_ready(present_set):
                readiness = "READY_DRY_RUN_ONLY"
            else:
                readiness = "PARTIAL_DRY_RUN_ONLY"

        # ── Blocking reason codes ─────────────────────────────────────────────
        blocking: list[str] = list(_ALWAYS_BLOCKING)
        for b in missing_b:
            if b in EXPECTED_BUCKETS:
                blocking.append(f"missing_bucket_{b}")
        blocking = sorted(blocking)

        if fact_count > 0:
            tickers_with_any_source_linked_evidence_count += 1
        if readiness == "READY_DRY_RUN_ONLY":
            tickers_ready_for_future_adapter_count += 1

        by_ticker_out[ticker] = {
            "source_linked_metric_fact_count": fact_count,
            "present_buckets": present_b,
            "missing_buckets": missing_b,
            "present_bucket_groups": sorted(present_groups),
            "missing_bucket_groups": sorted(missing_groups),
            "forms": dict(ticker_forms.get(ticker, {})),
            "units": dict(ticker_units.get(ticker, {})),
            "future_adapter_readiness": readiness,
            "blocking_reason_codes": blocking,
        }

    tickers_evaluated_count = len(all_tickers)

    return SecMetricEvidenceSnapshotDryRunResult(
        snapshot_enabled=True,
        snapshot_safe_for_decision=False,
        visible_snapshot_unchanged=True,
        tickers_evaluated_count=tickers_evaluated_count,
        tickers_with_any_source_linked_evidence_count=tickers_with_any_source_linked_evidence_count,
        tickers_ready_for_future_adapter_count=tickers_ready_for_future_adapter_count,
        tickers_blocked_from_decision_count=tickers_evaluated_count,  # always all
        by_ticker=by_ticker_out,
    )


def _is_ready(present_set: set[str]) -> bool:
    """Return True iff all three group readiness conditions are met."""
    # income_statement_core: revenue + at least one of operating_income/net_income/eps
    income_ok = (
        "revenue" in present_set
        and bool(present_set & _INCOME_STMT_OPTIONAL)
    )
    # cash_flow_core: operating_cash_flow AND capex
    cash_flow_ok = (
        "operating_cash_flow" in present_set and "capex" in present_set
    )
    # balance_sheet_core: all four required
    balance_ok = _BALANCE_SHEET_REQUIRED.issubset(present_set)
    return income_ok and cash_flow_ok and balance_ok
