"""Phase 8A — SEC CompanyFacts Metric Truth Adapter Dry Run.

Purpose:
    Read-only, deterministic mapper that takes pre-fetched metric_observation
    facts and normalizes their SEC XBRL tags into internal evidence buckets for
    future truth-adapter design.

    Answers:
    - Which tickers have source-linked SEC metric evidence?
    - Which normalized financial evidence buckets are covered?
    - Which SEC tags map into each bucket?
    - Which forms/units back the mapped evidence?
    - Which expected buckets are missing per ticker?
    - Are all mapped outputs still blocked from decision consumption?

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
    - dry_run_safe_for_decision is always False.
    - visible_snapshot_unchanged is always True.
    - Unknown/unmapped tags are counted separately — never dropped silently.
    - Missing tag/unit/form is recorded as UNKNOWN, not a crash.
    - Only source-linked facts (source_id present) are counted in adapter totals.
    - Only fact_kind == "metric_observation" AND claim == "sec_companyfact_observed"
      facts are processed.
    - Non-metric facts and non-companyfacts claims are silently ignored.
    - Any exception triggers a safe empty result — never propagates to callers.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ── Normalized evidence bucket mapping ───────────────────────────────────────
# Maps SEC XBRL us-gaap tag → internal evidence bucket name.
# Only the listed tags are mapped; all others count as unmapped.
# Do not add aliases, inferred tags, or computed tags here — only direct mappings.
SEC_METRIC_TRUTH_ADAPTER_DRY_RUN_CONTRACT_VERSION = "phase8a_v1"

SEC_METRIC_BUCKET_MAP: dict[str, str] = {
    "Revenues": "revenue",
    "RevenueFromContractWithCustomerExcludingAssessedTax": "revenue",
    "NetIncomeLoss": "net_income",
    "OperatingIncomeLoss": "operating_income",
    "EarningsPerShareBasic": "eps",
    "EarningsPerShareDiluted": "eps",
    "NetCashProvidedByUsedInOperatingActivities": "operating_cash_flow",
    "PaymentsToAcquirePropertyPlantAndEquipment": "capex",
    "CashAndCashEquivalentsAtCarryingValue": "cash",
    "Assets": "assets",
    "Liabilities": "liabilities",
    "StockholdersEquity": "equity",
}

EXPECTED_BUCKETS: frozenset[str] = frozenset(SEC_METRIC_BUCKET_MAP.values())


@dataclass
class SecMetricTruthAdapterDryRunResult:
    """Aggregate-only dry-run result for Phase 8A.

    All fields are safe to log and return via the diagnostics endpoint.

    Forbidden (never present in any field):
        - raw metric values
        - full structured_payload dicts
        - raw source URLs or excerpts
        - raw DB rows
        - Buy/Hold/Trim/Sell signals
        - user-facing UI copy

    Invariants:
        dry_run_safe_for_decision is always False.
        visible_snapshot_unchanged is always True.
    """
    dry_run_enabled: bool
    dry_run_safe_for_decision: bool           # always False
    artifacts_evaluated_count: int
    source_linked_metric_fact_count: int      # only source-linked + companyfacts claim
    unmapped_metric_fact_count: int           # source-linked companyfacts but unknown tag
    by_ticker: dict[str, int]                 # ticker → source-linked fact count
    by_bucket: dict[str, int]                 # bucket → mapped fact count
    by_tag: dict[str, int]                    # SEC tag → source-linked fact count
    by_unit: dict[str, int]                   # unit → source-linked fact count
    by_form: dict[str, int]                   # form → source-linked fact count
    missing_buckets_by_ticker: dict[str, list[str]]  # ticker → missing expected buckets
    visible_snapshot_unchanged: bool          # always True


def run_sec_metric_truth_adapter_dry_run(
    artifact_rows: list[dict],
    facts_by_artifact: dict[str, list[dict]],
) -> SecMetricTruthAdapterDryRunResult:
    """Pure, deterministic, read-only Phase 8A dry-run adapter.

    Takes pre-fetched artifact rows and their associated facts. Returns
    aggregate-only bucket/tag/unit/form counts. Never raises.

    Args:
        artifact_rows:      List of artifact dicts (research_artifacts rows).
        facts_by_artifact:  Mapping from artifact_id str → list of fact dicts.
                            Each fact dict has: fact_kind, structured_payload,
                            source_id (and optionally artifact_id, id).

    Returns:
        SecMetricTruthAdapterDryRunResult with aggregate-only counts.
        dry_run_safe_for_decision is always False.
        visible_snapshot_unchanged is always True.
    """
    try:
        return _run(artifact_rows, facts_by_artifact)
    except Exception:  # noqa: BLE001
        return SecMetricTruthAdapterDryRunResult(
            dry_run_enabled=True,
            dry_run_safe_for_decision=False,
            artifacts_evaluated_count=0,
            source_linked_metric_fact_count=0,
            unmapped_metric_fact_count=0,
            by_ticker={},
            by_bucket={},
            by_tag={},
            by_unit={},
            by_form={},
            missing_buckets_by_ticker={},
            visible_snapshot_unchanged=True,
        )


def _run(
    artifact_rows: list[dict],
    facts_by_artifact: dict[str, list[dict]],
) -> SecMetricTruthAdapterDryRunResult:
    artifacts_evaluated_count = len(artifact_rows)
    source_linked_metric_fact_count = 0
    unmapped_metric_fact_count = 0
    by_ticker: dict[str, int] = {}
    by_bucket: dict[str, int] = {}
    by_tag: dict[str, int] = {}
    by_unit: dict[str, int] = {}
    by_form: dict[str, int] = {}
    covered_buckets_by_ticker: dict[str, set[str]] = {}

    for artifact_row in artifact_rows:
        aid = str(artifact_row.get("id", ""))
        ticker = str(artifact_row.get("ticker") or "UNKNOWN")
        artifact_facts = facts_by_artifact.get(aid, [])

        for fact in artifact_facts:
            # Only metric_observation facts.
            if str(fact.get("fact_kind") or "") != "metric_observation":
                continue

            sp = fact.get("structured_payload")
            # Only CompanyFacts claims.
            if not isinstance(sp, dict) or sp.get("claim") != "sec_companyfact_observed":
                continue

            # Only source-linked facts.
            source_id = fact.get("source_id")
            if not source_id or not str(source_id).strip():
                continue

            # Aggregate-only: extract label strings, never raw numeric values.
            tag = str(sp.get("tag") or "UNKNOWN")
            unit = str(sp.get("unit") or "UNKNOWN")
            form = str(sp.get("form") or "UNKNOWN")

            source_linked_metric_fact_count += 1
            by_ticker[ticker] = by_ticker.get(ticker, 0) + 1
            by_tag[tag] = by_tag.get(tag, 0) + 1
            by_unit[unit] = by_unit.get(unit, 0) + 1
            by_form[form] = by_form.get(form, 0) + 1

            bucket = SEC_METRIC_BUCKET_MAP.get(tag)
            if bucket is None:
                unmapped_metric_fact_count += 1
            else:
                by_bucket[bucket] = by_bucket.get(bucket, 0) + 1
                covered_buckets_by_ticker.setdefault(ticker, set()).add(bucket)

    # Missing buckets per ticker — deterministic sorted list.
    missing_buckets_by_ticker: dict[str, list[str]] = {}
    for ticker in by_ticker:
        covered = covered_buckets_by_ticker.get(ticker, set())
        missing = sorted(EXPECTED_BUCKETS - covered)
        if missing:
            missing_buckets_by_ticker[ticker] = missing

    return SecMetricTruthAdapterDryRunResult(
        dry_run_enabled=True,
        dry_run_safe_for_decision=False,
        artifacts_evaluated_count=artifacts_evaluated_count,
        source_linked_metric_fact_count=source_linked_metric_fact_count,
        unmapped_metric_fact_count=unmapped_metric_fact_count,
        by_ticker=by_ticker,
        by_bucket=by_bucket,
        by_tag=by_tag,
        by_unit=by_unit,
        by_form=by_form,
        missing_buckets_by_ticker=missing_buckets_by_ticker,
        visible_snapshot_unchanged=True,
    )
