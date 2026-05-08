"""Phase 8D — SEC Metric Portfolio Coverage Dry Run.

Purpose:
    Compares the current portfolio ticker universe against Phase 8B SEC metric
    evidence snapshot output to produce a portfolio-wide coverage diagnostic.

    Answers:
    - Which portfolio tickers have any SEC research artifacts?
    - Which portfolio tickers have source-linked SEC metric evidence?
    - Which portfolio tickers are READY/PARTIAL/BLOCKED for future adapter?
    - Which portfolio tickers have no SEC artifact coverage at all?
    - Which tickers are blocked because of asset type (ETF/Crypto), if determinable?
    - What is the portfolio-wide readiness distribution?
    - Is the evidence lane still fully blocked from decision consumption?

    This is a dry run only. MUST NOT feed anything into decide(), visible
    snapshots, recommendation cards, portfolio actions, or any Buy/Hold/Trim/Sell
    policy.

Architecture invariants (non-negotiable):
    - NEVER imports or calls decide() from decision_policy_v1.
    - NEVER imports IntelV3Service, recommendation_engine, or any frontend path.
    - NEVER writes to any DB table.
    - NEVER feeds normalized evidence into visible decisions.
    - NEVER returns raw metric values, structured_payload, source URLs, excerpts.
    - NEVER returns raw DB rows.
    - NEVER sets safe_for_decision=True.
    - safe_for_decision is always False.
    - visible_snapshot_unchanged is always True.
    - Any exception triggers a safe empty result — never propagates to callers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .sec_metric_candidate_classifier import classify_sec_metric_candidate
from .sec_metric_evidence_snapshot_dry_run import run_sec_metric_evidence_snapshot_dry_run
from .sec_metric_truth_adapter_dry_run import EXPECTED_BUCKETS, run_sec_metric_truth_adapter_dry_run

SEC_METRIC_PORTFOLIO_COVERAGE_DRY_RUN_CONTRACT_VERSION = "phase8d_v1"

# Always-present blocking codes on every portfolio ticker — evidence lane is closed.
_ALWAYS_BLOCKING: tuple[str, ...] = (
    "decision_consumption_disabled",
    "safe_for_decision_db_lock",
)


@dataclass
class PortfolioSecCoverageDryRunResult:
    """Aggregate-only portfolio SEC coverage dry-run result for Phase 8D.

    All fields are safe to log and return via the diagnostics endpoint.

    Forbidden (never present in any field):
        - raw metric values
        - full structured_payload dicts
        - raw source URLs or excerpts
        - raw DB rows
        - Buy/Hold/Trim/Sell signals
        - user-facing UI copy

    Invariants:
        safe_for_decision is always False.
        visible_snapshot_unchanged is always True.
    """
    coverage_enabled: bool
    safe_for_decision: bool                       # always False
    visible_snapshot_unchanged: bool              # always True
    portfolio_ticker_count: int
    portfolio_tickers_evaluated: list[str]        # sorted, deduplicated
    # SEC-specific coverage counts.
    # A ticker counts as having SEC research artifacts only if it has at least one
    # source-linked sec_companyfact_observed metric fact (fact_count > 0 in Phase 8B).
    # Tickers with only generic artifacts (no SEC metric facts) are counted as without.
    tickers_with_sec_research_artifacts_count: int
    tickers_without_sec_research_artifacts_count: int
    tickers_with_source_linked_metric_evidence_count: int
    tickers_ready_for_future_adapter_count: int
    tickers_partial_for_future_adapter_count: int
    tickers_blocked_for_future_adapter_count: int
    tickers_without_sec_metric_coverage: list[str]  # sorted
    readiness_counts: dict[str, int]
    by_ticker: dict[str, dict]
    errors: list[str] = field(default_factory=list)


def _disabled_result(reason: str) -> PortfolioSecCoverageDryRunResult:
    """Return a no-op result when coverage is disabled."""
    return PortfolioSecCoverageDryRunResult(
        coverage_enabled=False,
        safe_for_decision=False,
        visible_snapshot_unchanged=True,
        portfolio_ticker_count=0,
        portfolio_tickers_evaluated=[],
        tickers_with_sec_research_artifacts_count=0,
        tickers_without_sec_research_artifacts_count=0,
        tickers_with_source_linked_metric_evidence_count=0,
        tickers_ready_for_future_adapter_count=0,
        tickers_partial_for_future_adapter_count=0,
        tickers_blocked_for_future_adapter_count=0,
        tickers_without_sec_metric_coverage=[],
        readiness_counts={},
        by_ticker={},
        errors=[reason],
    )


def build_portfolio_sec_coverage_dry_run(
    portfolio_positions: list[dict],
    snapshot_by_ticker: dict[str, dict],
    coverage_enabled: bool,
) -> PortfolioSecCoverageDryRunResult:
    """Pure, deterministic, read-only Phase 8D portfolio coverage builder.

    Takes portfolio positions and the Phase 8B by_ticker snapshot output.
    Returns aggregate-only portfolio coverage diagnostics. Never raises.

    Args:
        portfolio_positions: List of dicts with 'ticker' and optionally 'category'.
                             category values: Crypto|Core|ETF|Other|IPO|SELL.
        snapshot_by_ticker:  Phase 8B by_ticker output (ticker → evidence snapshot dict).
                             Tickers absent from this dict have no research artifacts.
        coverage_enabled:    Flag state to report back in the result.

    Returns:
        PortfolioSecCoverageDryRunResult with portfolio coverage summary.
        safe_for_decision is always False.
        visible_snapshot_unchanged is always True.
    """
    try:
        return _build(portfolio_positions, snapshot_by_ticker, coverage_enabled)
    except Exception:  # noqa: BLE001
        return PortfolioSecCoverageDryRunResult(
            coverage_enabled=coverage_enabled,
            safe_for_decision=False,
            visible_snapshot_unchanged=True,
            portfolio_ticker_count=0,
            portfolio_tickers_evaluated=[],
            tickers_with_sec_research_artifacts_count=0,
            tickers_without_sec_research_artifacts_count=0,
            tickers_with_source_linked_metric_evidence_count=0,
            tickers_ready_for_future_adapter_count=0,
            tickers_partial_for_future_adapter_count=0,
            tickers_blocked_for_future_adapter_count=0,
            tickers_without_sec_metric_coverage=[],
            readiness_counts={},
            by_ticker={},
            errors=["coverage_build_error"],
        )


def _build(
    portfolio_positions: list[dict],
    snapshot_by_ticker: dict[str, dict],
    coverage_enabled: bool,
) -> PortfolioSecCoverageDryRunResult:
    # Deduplicate and sort portfolio tickers; preserve first-seen category per ticker.
    seen: dict[str, str] = {}
    for pos in portfolio_positions:
        ticker = str(pos.get("ticker") or "").upper().strip()
        category = str(pos.get("category") or "")
        if ticker and ticker not in seen:
            seen[ticker] = category

    sorted_tickers = sorted(seen.keys())

    tickers_with_sec_research_artifacts_count = 0
    tickers_without_sec_research_artifacts_count = 0
    tickers_with_source_linked_metric_evidence_count = 0
    tickers_ready_for_future_adapter_count = 0
    tickers_partial_for_future_adapter_count = 0
    tickers_blocked_for_future_adapter_count = 0
    tickers_without_sec_metric_coverage: list[str] = []
    by_ticker_out: dict[str, dict] = {}

    for ticker in sorted_tickers:
        category = seen[ticker]
        phase8b_data = snapshot_by_ticker.get(ticker)

        # SEC coverage is defined by source-linked SEC CompanyFacts metric evidence,
        # not merely by the presence of any research artifact row.
        # A ticker with only generic artifacts (no sec_companyfact_observed metric facts)
        # has fact_count == 0 in Phase 8B and is treated as lacking SEC coverage.
        if phase8b_data is not None:
            fact_count = int(phase8b_data.get("source_linked_metric_fact_count") or 0)
            readiness: str = str(
                phase8b_data.get("future_adapter_readiness") or "BLOCKED_DRY_RUN_ONLY"
            )
            present_buckets: list[str] = list(phase8b_data.get("present_buckets") or [])
            missing_buckets: list[str] = list(phase8b_data.get("missing_buckets") or [])
            base_blocking: list[str] = list(
                phase8b_data.get("blocking_reason_codes") or list(_ALWAYS_BLOCKING)
            )
        else:
            fact_count = 0
            readiness = "BLOCKED_DRY_RUN_ONLY"
            present_buckets = []
            missing_buckets = sorted(EXPECTED_BUCKETS)
            base_blocking = list(_ALWAYS_BLOCKING)

        # SEC research artifact coverage: only true when Phase 8B shows at least one
        # source-linked SEC CompanyFacts metric fact.
        has_sec_research_artifacts = fact_count > 0
        has_evidence = has_sec_research_artifacts

        if has_sec_research_artifacts:
            tickers_with_sec_research_artifacts_count += 1
            tickers_with_source_linked_metric_evidence_count += 1
        else:
            # No SEC metric evidence: either no artifact at all, or artifact exists but
            # has no source-linked sec_companyfact_observed metric facts.
            tickers_without_sec_research_artifacts_count += 1
            tickers_without_sec_metric_coverage.append(ticker)
            # Add missing_sec_research_artifact if not already in base_blocking
            # (it may already be present if Phase 8B produced it; ensure it's there).
            if "missing_sec_research_artifact" not in base_blocking:
                base_blocking = list(base_blocking) + ["missing_sec_research_artifact"]

        if readiness == "READY_DRY_RUN_ONLY":
            tickers_ready_for_future_adapter_count += 1
        elif readiness == "PARTIAL_DRY_RUN_ONLY":
            tickers_partial_for_future_adapter_count += 1
        else:
            tickers_blocked_for_future_adapter_count += 1

        # Add asset-type reason codes via shared classifier (category + symbol override).
        # Phase 8F: known fund/ETF/crypto tickers classified as non-company even if
        # their portfolio category is wrong or missing (e.g., VUG as Core).
        blocking_set = set(base_blocking)
        clf = classify_sec_metric_candidate(ticker, category)
        blocking_set.update(clf["blocking_reason_codes"])

        # Phase 8F: distinguish "attempted but no source-linked SEC metric evidence"
        # from "no artifact ever created". Only added for sec_company_like tickers
        # (not ETF/fund/crypto) that have a Phase 8B snapshot entry with fact_count==0.
        if phase8b_data is not None and fact_count == 0 and clf["is_sec_company_candidate"]:
            blocking_set.add("attempted_no_source_linked_sec_metric_evidence")
            blocking_set.add("manual_review_required_before_retry")

        blocking_codes = sorted(blocking_set)

        by_ticker_out[ticker] = {
            "has_sec_research_artifacts": has_sec_research_artifacts,
            "has_source_linked_metric_evidence": has_evidence,
            "source_linked_metric_fact_count": fact_count,
            "future_adapter_readiness": readiness,
            "present_buckets": sorted(present_buckets),
            "missing_buckets": sorted(missing_buckets),
            "blocking_reason_codes": blocking_codes,
        }

    readiness_counts = {
        "READY_DRY_RUN_ONLY": tickers_ready_for_future_adapter_count,
        "PARTIAL_DRY_RUN_ONLY": tickers_partial_for_future_adapter_count,
        "BLOCKED_DRY_RUN_ONLY": tickers_blocked_for_future_adapter_count,
    }

    return PortfolioSecCoverageDryRunResult(
        coverage_enabled=coverage_enabled,
        safe_for_decision=False,
        visible_snapshot_unchanged=True,
        portfolio_ticker_count=len(sorted_tickers),
        portfolio_tickers_evaluated=sorted_tickers,
        tickers_with_sec_research_artifacts_count=tickers_with_sec_research_artifacts_count,
        tickers_without_sec_research_artifacts_count=tickers_without_sec_research_artifacts_count,
        tickers_with_source_linked_metric_evidence_count=tickers_with_source_linked_metric_evidence_count,
        tickers_ready_for_future_adapter_count=tickers_ready_for_future_adapter_count,
        tickers_partial_for_future_adapter_count=tickers_partial_for_future_adapter_count,
        tickers_blocked_for_future_adapter_count=tickers_blocked_for_future_adapter_count,
        tickers_without_sec_metric_coverage=sorted(tickers_without_sec_metric_coverage),
        readiness_counts=readiness_counts,
        by_ticker=by_ticker_out,
    )


def compute_portfolio_sec_metric_coverage(
    user_id: str,
    db_client: Any,
    settings: Any = None,
) -> PortfolioSecCoverageDryRunResult:
    """Read portfolio tickers and research artifacts, then compute Phase 8D coverage.

    Returns PortfolioSecCoverageDryRunResult regardless of outcome. Never raises.

    Kill switch:
        settings.intel_v3_sec_metric_portfolio_coverage_dry_run_enabled must be True.

    This function:
        - Reads portfolio positions (ticker, category) from 'positions' table.
        - Falls back to 'portfolio_snapshots' positions_data if positions empty.
        - Reads research artifacts for portfolio tickers.
        - Reads facts for those artifacts.
        - Runs Phase 8A (sec_metric_truth_adapter_dry_run).
        - Runs Phase 8B (sec_metric_evidence_snapshot_dry_run).
        - Calls build_portfolio_sec_coverage_dry_run() to produce coverage.

    Never:
        - Writes to any DB table.
        - Calls SEC providers, LLMs, decide(), IntelV3Service.
        - Returns raw metric values, structured_payload, source URLs.
        - Sets safe_for_decision=True.
    """
    if settings is None:
        from app.config import get_settings
        settings = get_settings()

    if not getattr(settings, "intel_v3_sec_metric_portfolio_coverage_dry_run_enabled", False):
        return _disabled_result(
            "intel_v3_sec_metric_portfolio_coverage_dry_run_enabled=false"
        )

    errors: list[str] = []

    # ── Step 1: Read portfolio positions ──────────────────────────────────────
    portfolio_positions: list[dict] = []
    try:
        pos_result = (
            db_client.table("positions")
            .select("ticker,category")
            .eq("user_id", user_id)
            .execute()
        )
        raw_positions = list(pos_result.data or [])
        portfolio_positions = [
            {
                "ticker": str(r.get("ticker") or ""),
                "category": str(r.get("category") or ""),
            }
            for r in raw_positions
            if r.get("ticker")
        ]
    except Exception as exc:  # noqa: BLE001
        errors.append(f"portfolio_positions_query_error error={exc}")

    # Fallback: portfolio_snapshots.positions_data (most recent snapshot).
    if not portfolio_positions:
        try:
            snap_result = (
                db_client.table("portfolio_snapshots")
                .select("positions_data")
                .eq("user_id", user_id)
                .order("snapshot_at", desc=True)
                .limit(1)
                .execute()
            )
            if snap_result.data:
                raw_snap = snap_result.data[0] or {}
                raw_positions_data = raw_snap.get("positions_data") or []
                if isinstance(raw_positions_data, list):
                    for row in raw_positions_data:
                        if isinstance(row, dict) and row.get("ticker"):
                            portfolio_positions.append(
                                {
                                    "ticker": str(row["ticker"]),
                                    "category": str(row.get("category") or ""),
                                }
                            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"portfolio_snapshots_query_error error={exc}")

    # Deduplicate portfolio tickers for artifact query.
    portfolio_tickers = list(
        dict.fromkeys(
            p["ticker"].upper().strip()
            for p in portfolio_positions
            if p.get("ticker", "").strip()
        )
    )

    # ── Step 2: Read research artifacts for portfolio tickers ─────────────────
    artifact_rows: list[dict] = []
    if portfolio_tickers:
        try:
            art_result = (
                db_client.table("research_artifacts")
                .select(
                    "id,ticker,artifact_type,skill_pack,"
                    "safe_for_decision,is_active,created_at"
                )
                .eq("user_id", user_id)
                .in_("ticker", portfolio_tickers)
                .execute()
            )
            artifact_rows = list(art_result.data or [])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"research_artifacts_query_error error={exc}")

    # ── Step 3: Read facts for those artifacts ────────────────────────────────
    facts_by_artifact: dict[str, list[dict]] = {}
    if artifact_rows:
        artifact_ids = [str(r["id"]) for r in artifact_rows if r.get("id")]
        if artifact_ids:
            try:
                fact_result = (
                    db_client.table("research_artifact_facts")
                    .select("id,artifact_id,fact_kind,structured_payload,source_id")
                    .eq("user_id", user_id)
                    .in_("artifact_id", artifact_ids)
                    .execute()
                )
                for row in (fact_result.data or []):
                    aid = str(row.get("artifact_id", ""))
                    if aid:
                        facts_by_artifact.setdefault(aid, []).append(row)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"research_artifact_facts_query_error error={exc}")

    # ── Step 4: Run Phase 8A ──────────────────────────────────────────────────
    adapter_result = run_sec_metric_truth_adapter_dry_run(
        artifact_rows=artifact_rows,
        facts_by_artifact=facts_by_artifact,
    )

    # ── Step 5: Run Phase 8B ──────────────────────────────────────────────────
    snapshot_result = run_sec_metric_evidence_snapshot_dry_run(
        adapter_result=adapter_result,
        artifact_rows=artifact_rows,
        facts_by_artifact=facts_by_artifact,
    )

    # ── Step 6: Build portfolio coverage ─────────────────────────────────────
    coverage = build_portfolio_sec_coverage_dry_run(
        portfolio_positions=portfolio_positions,
        snapshot_by_ticker=snapshot_result.by_ticker,
        coverage_enabled=True,
    )

    # Attach any DB-read errors accumulated above.
    coverage.errors.extend(errors)
    return coverage
