"""Phase 8E — SEC Metric Portfolio Coverage Expansion.

Purpose:
    Protected operator-only path that selects eligible SEC-company portfolio
    tickers missing SEC CompanyFacts metric_observation evidence and runs the
    existing Phase 3/7A artifact writer for each selected ticker.

    Eligible tickers = portfolio tickers that:
      - are NOT categorized as ETF or Crypto
      - do NOT already have source-linked SEC metric evidence (fact_count > 0)
      - are NOT already READY_DRY_RUN_ONLY or PARTIAL_DRY_RUN_ONLY

    This is pre-consumption only:
      - Writes research artifacts/facts only.
      - safe_for_decision remains False (DB constraint + writer).
      - Does NOT feed artifacts into decisions.
      - Does NOT modify visible Intel v3 actions/copy/snapshot/UI.

Architecture invariants (non-negotiable):
    - NEVER imports or calls decide() from decision_policy_v1.
    - NEVER imports IntelV3Service, recommendation_engine, or any frontend path.
    - NEVER writes to intel_v3_snapshots.
    - NEVER feeds normalized evidence into visible decisions.
    - NEVER returns raw metric values, structured_payload, source URLs, excerpts.
    - NEVER returns raw DB rows.
    - NEVER sets safe_for_decision=True.
    - safe_for_decision is always False.
    - visible_snapshot_unchanged is always True.
    - Any exception triggers a safe disabled result — never propagates to callers.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

from .sec_metric_truth_adapter_dry_run import run_sec_metric_truth_adapter_dry_run
from .sec_metric_evidence_snapshot_dry_run import run_sec_metric_evidence_snapshot_dry_run

SEC_METRIC_COVERAGE_EXPANSION_CONTRACT_VERSION = "phase8e_v1"

# Hard cap applied after all other candidate filters — never exceed this count.
MAX_TICKERS_PER_EXPANSION: int = 10

# Category values that indicate non-SEC-company assets.
_ETF_CATEGORIES: frozenset[str] = frozenset({"ETF"})
_CRYPTO_CATEGORIES: frozenset[str] = frozenset({"Crypto"})

# Readiness values that mean the ticker already has meaningful SEC evidence.
_ALREADY_COVERED_READINESS: frozenset[str] = frozenset({
    "READY_DRY_RUN_ONLY",
    "PARTIAL_DRY_RUN_ONLY",
})


@dataclass
class CoverageExpansionResult:
    """Aggregate-only Phase 8E coverage expansion result.

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
    coverage_expansion_enabled: bool
    dry_run: bool
    safe_for_decision: bool                    # always False
    visible_snapshot_unchanged: bool           # always True
    portfolio_ticker_count: int
    candidate_count: int
    selected_tickers: list[str]               # sorted, deterministic
    skipped_tickers_by_reason: dict[str, list[str]]  # reason → sorted ticker list
    attempted_count: int
    written_count: int
    skipped_count: int                        # total skipped (not selected)
    failed_count: int
    artifact_ids: list[str]
    safe_for_decision_false_count: int
    unexpected_safe_for_decision_true_count: int
    forbidden_payload_violation_count: int
    before_coverage_summary: dict
    after_coverage_summary: Optional[dict]
    errors: list[str] = field(default_factory=list)


def _disabled_result(reason: str) -> CoverageExpansionResult:
    """Return a no-op result when expansion is disabled."""
    return CoverageExpansionResult(
        coverage_expansion_enabled=False,
        dry_run=True,
        safe_for_decision=False,
        visible_snapshot_unchanged=True,
        portfolio_ticker_count=0,
        candidate_count=0,
        selected_tickers=[],
        skipped_tickers_by_reason={},
        attempted_count=0,
        written_count=0,
        skipped_count=0,
        failed_count=0,
        artifact_ids=[],
        safe_for_decision_false_count=0,
        unexpected_safe_for_decision_true_count=0,
        forbidden_payload_violation_count=0,
        before_coverage_summary={},
        after_coverage_summary=None,
        errors=[reason],
    )


def _build_coverage_summary(
    artifact_rows: list[dict],
    facts_by_artifact: dict[str, list[dict]],
    portfolio_positions: list[dict],
) -> dict:
    """Build an aggregate-only coverage summary from in-memory data.

    Runs Phase 8A + 8B pure functions and returns a compact summary dict.
    Never returns raw metric values, payloads, or source URLs.
    """
    adapter_result = run_sec_metric_truth_adapter_dry_run(
        artifact_rows=artifact_rows,
        facts_by_artifact=facts_by_artifact,
    )
    snapshot_result = run_sec_metric_evidence_snapshot_dry_run(
        adapter_result=adapter_result,
        artifact_rows=artifact_rows,
        facts_by_artifact=facts_by_artifact,
    )

    # Count portfolio tickers vs snapshot.
    seen_tickers: dict[str, str] = {}
    for pos in portfolio_positions:
        ticker = str(pos.get("ticker") or "").upper().strip()
        category = str(pos.get("category") or "")
        if ticker and ticker not in seen_tickers:
            seen_tickers[ticker] = category

    with_evidence = 0
    without_evidence = 0
    ready_count = 0
    partial_count = 0
    blocked_count = 0

    for ticker in seen_tickers:
        snap = snapshot_result.by_ticker.get(ticker)
        if snap is None:
            without_evidence += 1
            blocked_count += 1
        else:
            fc = int(snap.get("source_linked_metric_fact_count") or 0)
            readiness = str(snap.get("future_adapter_readiness") or "BLOCKED_DRY_RUN_ONLY")
            if fc > 0:
                with_evidence += 1
            else:
                without_evidence += 1
            if readiness == "READY_DRY_RUN_ONLY":
                ready_count += 1
            elif readiness == "PARTIAL_DRY_RUN_ONLY":
                partial_count += 1
            else:
                blocked_count += 1

    return {
        "portfolio_ticker_count": len(seen_tickers),
        "tickers_with_source_linked_metric_evidence_count": with_evidence,
        "tickers_without_source_linked_metric_evidence_count": without_evidence,
        "readiness_counts": {
            "READY_DRY_RUN_ONLY": ready_count,
            "PARTIAL_DRY_RUN_ONLY": partial_count,
            "BLOCKED_DRY_RUN_ONLY": blocked_count,
        },
    }


def _read_portfolio_and_artifacts(
    user_id: str,
    db_client: Any,
    errors: list[str],
) -> tuple[list[dict], list[dict], dict[str, list[dict]]]:
    """Read portfolio positions, research artifacts, and facts from DB.

    Returns (portfolio_positions, artifact_rows, facts_by_artifact).
    Never raises — errors are appended to the errors list.
    """
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

    portfolio_tickers = list(
        dict.fromkeys(
            p["ticker"].upper().strip()
            for p in portfolio_positions
            if p.get("ticker", "").strip()
        )
    )

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

    return portfolio_positions, artifact_rows, facts_by_artifact


def _select_candidates(
    portfolio_positions: list[dict],
    snapshot_by_ticker: dict[str, dict],
    include_tickers: list[str],
    exclude_tickers: list[str],
    max_tickers: int,
) -> tuple[list[str], dict[str, list[str]]]:
    """Select eligible expansion candidates from portfolio positions.

    Returns (selected_tickers, skipped_tickers_by_reason).
    selected_tickers is deterministically sorted.
    skipped_tickers_by_reason maps reason → sorted ticker list.
    """
    # Normalize filter sets.
    include_set = frozenset(t.upper().strip() for t in include_tickers if t.strip())
    exclude_set = frozenset(t.upper().strip() for t in exclude_tickers if t.strip())

    # Deduplicate portfolio tickers, preserving first-seen category.
    seen: dict[str, str] = {}
    for pos in portfolio_positions:
        ticker = str(pos.get("ticker") or "").upper().strip()
        category = str(pos.get("category") or "")
        if ticker and ticker not in seen:
            seen[ticker] = category

    # Apply include_tickers filter: if non-empty, restrict to those tickers.
    # Tickers not in include_set are simply not considered — no skip reason recorded.
    if include_set:
        seen = {t: c for t, c in seen.items() if t in include_set}

    # Categorize each ticker.
    skip_reasons: dict[str, list[str]] = {}   # ticker → list of reasons
    eligible: list[str] = []

    for ticker in sorted(seen.keys()):
        category = seen[ticker]
        reasons: list[str] = []

        # Check asset type first (ETF/Crypto).
        if category in _ETF_CATEGORIES:
            reasons.extend(["asset_type_not_sec_company", "likely_fund_or_etf"])
        elif category in _CRYPTO_CATEGORIES:
            reasons.extend(["asset_type_not_sec_company", "likely_crypto"])

        # Check if already has SEC metric evidence.
        if not reasons:
            snap = snapshot_by_ticker.get(ticker)
            if snap is not None:
                fact_count = int(snap.get("source_linked_metric_fact_count") or 0)
                readiness = str(snap.get("future_adapter_readiness") or "BLOCKED_DRY_RUN_ONLY")
                if fact_count > 0 or readiness in _ALREADY_COVERED_READINESS:
                    reasons.append("already_has_sec_metric_evidence")

        # Check exclude_tickers.
        if ticker in exclude_set and "already_has_sec_metric_evidence" not in reasons:
            # Only add excluded_by_request if not already filtered by evidence check.
            if not reasons:
                reasons.append("excluded_by_request")

        if reasons:
            skip_reasons[ticker] = reasons
        else:
            eligible.append(ticker)

    # Apply max_tickers cap on sorted eligible list.
    eligible_sorted = sorted(eligible)
    selected = eligible_sorted[:max_tickers]
    over_cap = eligible_sorted[max_tickers:]

    for ticker in over_cap:
        skip_reasons[ticker] = ["over_max_tickers_cap"]

    # Build skipped_tickers_by_reason: reason → sorted list of tickers.
    reasons_to_tickers: dict[str, list[str]] = {}
    for ticker, reasons in skip_reasons.items():
        for reason in reasons:
            reasons_to_tickers.setdefault(reason, [])
            if ticker not in reasons_to_tickers[reason]:
                reasons_to_tickers[reason].append(ticker)

    # Sort each reason's ticker list deterministically.
    for reason in reasons_to_tickers:
        reasons_to_tickers[reason].sort()

    return selected, reasons_to_tickers


def compute_coverage_expansion(
    user_id: str,
    db_client: Any,
    max_tickers: int = MAX_TICKERS_PER_EXPANSION,
    include_tickers: Optional[list[str]] = None,
    exclude_tickers: Optional[list[str]] = None,
    dry_run: bool = True,
    settings: Any = None,
) -> CoverageExpansionResult:
    """Phase 8E portfolio coverage expansion.

    Selects eligible SEC-company portfolio tickers missing SEC metric evidence
    and (when dry_run=false) calls the existing Phase 3/7A artifact writer for
    each selected ticker.

    Returns CoverageExpansionResult regardless of outcome. Never raises.

    Kill switch:
        settings.intel_v3_sec_metric_portfolio_coverage_expansion_enabled must be True.

    Additional flags required for writes (dry_run=false):
        settings.intel_v3_research_workers_enabled must be True.
        settings.intel_v3_earnings_reviewer_enabled must be True.

    Never:
        - Imports or calls decide() / decision_policy_v1.
        - Imports IntelV3Service, recommendation_engine, or any frontend path.
        - Writes to intel_v3_snapshots.
        - Returns raw metric values, structured_payload, source URLs.
        - Sets safe_for_decision=True.
    """
    if settings is None:
        from app.config import get_settings
        settings = get_settings()

    if not getattr(settings, "intel_v3_sec_metric_portfolio_coverage_expansion_enabled", False):
        return _disabled_result("intel_v3_sec_metric_portfolio_coverage_expansion_enabled=false")

    # Clamp max_tickers defensively.
    effective_max = min(max(1, max_tickers), MAX_TICKERS_PER_EXPANSION)

    try:
        return _compute(
            user_id=user_id,
            db_client=db_client,
            max_tickers=effective_max,
            include_tickers=list(include_tickers or []),
            exclude_tickers=list(exclude_tickers or []),
            dry_run=dry_run,
            settings=settings,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("coverage_expansion_unexpected_error user_id=%s error=%s", user_id, exc)
        return CoverageExpansionResult(
            coverage_expansion_enabled=True,
            dry_run=dry_run,
            safe_for_decision=False,
            visible_snapshot_unchanged=True,
            portfolio_ticker_count=0,
            candidate_count=0,
            selected_tickers=[],
            skipped_tickers_by_reason={},
            attempted_count=0,
            written_count=0,
            skipped_count=0,
            failed_count=0,
            artifact_ids=[],
            safe_for_decision_false_count=0,
            unexpected_safe_for_decision_true_count=0,
            forbidden_payload_violation_count=0,
            before_coverage_summary={},
            after_coverage_summary=None,
            errors=[f"coverage_expansion_error error={exc}"],
        )


def _compute(
    user_id: str,
    db_client: Any,
    max_tickers: int,
    include_tickers: list[str],
    exclude_tickers: list[str],
    dry_run: bool,
    settings: Any,
) -> CoverageExpansionResult:
    errors: list[str] = []

    # ── Step 1: Read portfolio positions and existing artifacts/facts ──────────
    portfolio_positions, artifact_rows, facts_by_artifact = _read_portfolio_and_artifacts(
        user_id=user_id,
        db_client=db_client,
        errors=errors,
    )

    # ── Step 2: Run Phase 8A + 8B pure functions on existing data ─────────────
    adapter_result = run_sec_metric_truth_adapter_dry_run(
        artifact_rows=artifact_rows,
        facts_by_artifact=facts_by_artifact,
    )
    snapshot_result = run_sec_metric_evidence_snapshot_dry_run(
        adapter_result=adapter_result,
        artifact_rows=artifact_rows,
        facts_by_artifact=facts_by_artifact,
    )

    # ── Step 3: Build before_coverage_summary (aggregate-only) ────────────────
    before_summary = _build_coverage_summary(
        artifact_rows=artifact_rows,
        facts_by_artifact=facts_by_artifact,
        portfolio_positions=portfolio_positions,
    )

    # ── Step 4: Select candidates ──────────────────────────────────────────────
    selected_tickers, skipped_by_reason = _select_candidates(
        portfolio_positions=portfolio_positions,
        snapshot_by_ticker=snapshot_result.by_ticker,
        include_tickers=include_tickers,
        exclude_tickers=exclude_tickers,
        max_tickers=max_tickers,
    )

    # Deduplicate portfolio tickers for count.
    portfolio_tickers = list(
        dict.fromkeys(
            p["ticker"].upper().strip()
            for p in portfolio_positions
            if p.get("ticker", "").strip()
        )
    )
    skipped_count = len(portfolio_tickers) - len(selected_tickers)

    if dry_run:
        return CoverageExpansionResult(
            coverage_expansion_enabled=True,
            dry_run=True,
            safe_for_decision=False,
            visible_snapshot_unchanged=True,
            portfolio_ticker_count=len(portfolio_tickers),
            candidate_count=len(selected_tickers),
            selected_tickers=selected_tickers,
            skipped_tickers_by_reason=skipped_by_reason,
            attempted_count=0,
            written_count=0,
            skipped_count=skipped_count,
            failed_count=0,
            artifact_ids=[],
            safe_for_decision_false_count=0,
            unexpected_safe_for_decision_true_count=0,
            forbidden_payload_violation_count=0,
            before_coverage_summary=before_summary,
            after_coverage_summary=None,
            errors=errors,
        )

    # ── Step 5: dry_run=false — call existing SEC artifact writer per ticker ───
    from .runner import run_earnings_reviewer_dark

    artifact_ids: list[str] = []
    written_count = 0
    failed_count = 0
    safe_for_decision_false_count = 0
    unexpected_safe_for_decision_true_count = 0

    for ticker in selected_tickers:
        try:
            artifact_id = run_earnings_reviewer_dark(
                user_id=user_id,
                ticker=ticker,
                db_client=db_client,
                settings=settings,
            )
            if artifact_id is not None:
                written_count += 1
                artifact_ids.append(artifact_id)
                # Writer always hard-codes safe_for_decision=False.
                # DB constraint also enforces this.
                safe_for_decision_false_count += 1
                logger.info(
                    "coverage_expansion_write_success ticker=%s artifact_id=%s",
                    ticker,
                    artifact_id,
                )
            else:
                failed_count += 1
                errors.append(
                    f"coverage_expansion_write_none ticker={ticker} "
                    f"(check intel_v3_research_workers_enabled and "
                    f"intel_v3_earnings_reviewer_enabled flags)"
                )
        except Exception as exc:  # noqa: BLE001
            failed_count += 1
            errors.append(f"coverage_expansion_write_error ticker={ticker} error={exc}")

    # ── Step 6: Compute after_coverage_summary if any writes succeeded ─────────
    after_summary: Optional[dict] = None
    if written_count > 0:
        try:
            after_positions, after_artifacts, after_facts = _read_portfolio_and_artifacts(
                user_id=user_id,
                db_client=db_client,
                errors=errors,
            )
            after_summary = _build_coverage_summary(
                artifact_rows=after_artifacts,
                facts_by_artifact=after_facts,
                portfolio_positions=after_positions,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"after_coverage_summary_error error={exc}")

    return CoverageExpansionResult(
        coverage_expansion_enabled=True,
        dry_run=False,
        safe_for_decision=False,
        visible_snapshot_unchanged=True,
        portfolio_ticker_count=len(portfolio_tickers),
        candidate_count=len(selected_tickers),
        selected_tickers=selected_tickers,
        skipped_tickers_by_reason=skipped_by_reason,
        attempted_count=len(selected_tickers),
        written_count=written_count,
        skipped_count=skipped_count,
        failed_count=failed_count,
        artifact_ids=artifact_ids,
        safe_for_decision_false_count=safe_for_decision_false_count,
        unexpected_safe_for_decision_true_count=unexpected_safe_for_decision_true_count,
        forbidden_payload_violation_count=0,
        before_coverage_summary=before_summary,
        after_coverage_summary=after_summary,
        errors=errors,
    )
