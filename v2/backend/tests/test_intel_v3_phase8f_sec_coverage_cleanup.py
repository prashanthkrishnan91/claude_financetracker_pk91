"""Phase 8F — SEC Coverage Cleanup + Non-Company Classification Guardrails tests.

Acceptance criteria (13 groups):

 1. VUG miscategorized as Core/Other/empty is classified as non-company /
    likely_fund_or_etf in Phase 8D coverage diagnostics.
 2. VUG miscategorized as Core/Other/empty is skipped by Phase 8E expansion
    candidate selection and does not appear in selected_tickers.
 3. Existing category=ETF and category=Crypto behavior still works (no regression).
 4. BTC/XRP are classified as crypto-like even if category is wrong or empty.
 5. A normal SEC-company-like ticker with no snapshot and category Core remains
    eligible for Phase 8E expansion.
 6. A ticker with snapshot present and source_linked_metric_fact_count == 0 gets
    attempted_no_source_linked_sec_metric_evidence and
    manual_review_required_before_retry in Phase 8D diagnostics.
 7. BLSH/KLAR/TSM-style fixtures (snapshot present, fact_count == 0) are skipped
    by Phase 8E candidate selection under attempted/no-evidence reason.
 8. Existing READY/PARTIAL evidence-producing tickers are not reclassified.
 9. All relevant outputs remain deterministic and sorted.
10. Static import guard: no decide(), IntelV3Service, recommendation_engine,
    or frontend imports in Phase 8D/8E/classifier modules.
11. safe_for_decision remains False on all result paths.
12. visible_snapshot_unchanged remains True on all result paths.
13. No raw metric values, structured_payload, source URLs, excerpts, or raw rows
    are returned.

Architecture invariants verified by this file:
    - NEVER imports or calls decide() from decision_policy_v1.
    - NEVER imports IntelV3Service, recommendation_engine, or any frontend path.
    - NEVER sets safe_for_decision=True.
    - safe_for_decision always False.
    - visible_snapshot_unchanged always True.

All tests use in-memory fixtures — no Supabase dependency.
"""
from __future__ import annotations

import ast
import pathlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

# ── Imports under test ────────────────────────────────────────────────────────

from app.services.intelligence.research_workers.sec_metric_candidate_classifier import (
    KNOWN_CRYPTO_TICKERS,
    KNOWN_FUND_OR_ETF_TICKERS,
    classify_sec_metric_candidate,
)
from app.services.intelligence.research_workers.sec_metric_coverage_expansion import (
    _select_candidates,
)
from app.services.intelligence.research_workers.sec_metric_portfolio_coverage_dry_run import (
    build_portfolio_sec_coverage_dry_run,
)
from app.services.intelligence.research_workers.sec_metric_truth_adapter_dry_run import (
    SEC_METRIC_BUCKET_MAP,
    run_sec_metric_truth_adapter_dry_run,
)
from app.services.intelligence.research_workers.sec_metric_evidence_snapshot_dry_run import (
    run_sec_metric_evidence_snapshot_dry_run,
)

_UID = "u_phase8f"
_ALL_TAGS = list(SEC_METRIC_BUCKET_MAP.keys())

_RESEARCH_WORKERS_DIR = (
    pathlib.Path(__file__).parent.parent
    / "app/services/intelligence/research_workers"
)

# ── Fixture helpers ───────────────────────────────────────────────────────────


def _aid() -> str:
    return str(uuid.uuid4())


def _make_artifact(aid: str, ticker: str) -> dict:
    return {
        "id": aid,
        "user_id": _UID,
        "ticker": ticker,
        "artifact_type": "catalyst_window",
        "skill_pack": "earnings_reviewer",
        "safe_for_decision": False,
        "is_active": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _make_metric_fact(artifact_id: str, tag: str = "Revenues") -> dict:
    return {
        "id": str(uuid.uuid4()),
        "user_id": _UID,
        "artifact_id": artifact_id,
        "fact_kind": "metric_observation",
        "source_id": str(uuid.uuid4()),
        "structured_payload": {
            "claim": "sec_companyfact_observed",
            "taxonomy": "us-gaap",
            "tag": tag,
            "label": tag,
            "value": 123456789,
            "unit": "USD",
            "form": "10-K",
            "filed": "2024-11-01",
        },
    }


def _full_facts(aid: str) -> list[dict]:
    return [_make_metric_fact(aid, tag=t) for t in _ALL_TAGS]


def _run_phase8b(artifact_rows: list[dict], facts_by_artifact: dict) -> dict:
    adapter = run_sec_metric_truth_adapter_dry_run(
        artifact_rows=artifact_rows,
        facts_by_artifact=facts_by_artifact,
    )
    snapshot = run_sec_metric_evidence_snapshot_dry_run(
        adapter_result=adapter,
        artifact_rows=artifact_rows,
        facts_by_artifact=facts_by_artifact,
    )
    return snapshot.by_ticker


def _coverage(portfolio_positions: list[dict], snapshot_by_ticker: dict):
    return build_portfolio_sec_coverage_dry_run(
        portfolio_positions=portfolio_positions,
        snapshot_by_ticker=snapshot_by_ticker,
        coverage_enabled=True,
    )


def _select(
    positions: list[dict],
    snapshot_by_ticker: dict,
    include_tickers: list[str] | None = None,
    exclude_tickers: list[str] | None = None,
    max_tickers: int = 10,
):
    return _select_candidates(
        portfolio_positions=positions,
        snapshot_by_ticker=snapshot_by_ticker,
        include_tickers=include_tickers or [],
        exclude_tickers=exclude_tickers or [],
        max_tickers=max_tickers,
    )


def _read_module_src(filename: str) -> str:
    return (_RESEARCH_WORKERS_DIR / filename).read_text()


# =============================================================================
# AC 1 — VUG miscategorized as Core/Other/empty → Phase 8D: fund/ETF codes
# =============================================================================

class TestVugPhase8DDiagnostics:
    def test_vug_core_has_asset_type_not_sec_company(self):
        result = _coverage(
            portfolio_positions=[{"ticker": "VUG", "category": "Core"}],
            snapshot_by_ticker={},
        )
        codes = result.by_ticker["VUG"]["blocking_reason_codes"]
        assert "asset_type_not_sec_company" in codes, codes

    def test_vug_core_has_likely_fund_or_etf(self):
        result = _coverage(
            portfolio_positions=[{"ticker": "VUG", "category": "Core"}],
            snapshot_by_ticker={},
        )
        codes = result.by_ticker["VUG"]["blocking_reason_codes"]
        assert "likely_fund_or_etf" in codes, codes

    def test_vug_other_has_fund_etf_codes(self):
        result = _coverage(
            portfolio_positions=[{"ticker": "VUG", "category": "Other"}],
            snapshot_by_ticker={},
        )
        codes = result.by_ticker["VUG"]["blocking_reason_codes"]
        assert "asset_type_not_sec_company" in codes
        assert "likely_fund_or_etf" in codes

    def test_vug_empty_category_has_fund_etf_codes(self):
        result = _coverage(
            portfolio_positions=[{"ticker": "VUG", "category": ""}],
            snapshot_by_ticker={},
        )
        codes = result.by_ticker["VUG"]["blocking_reason_codes"]
        assert "asset_type_not_sec_company" in codes
        assert "likely_fund_or_etf" in codes

    def test_vug_no_likely_crypto(self):
        result = _coverage(
            portfolio_positions=[{"ticker": "VUG", "category": "Core"}],
            snapshot_by_ticker={},
        )
        codes = result.by_ticker["VUG"]["blocking_reason_codes"]
        assert "likely_crypto" not in codes

    def test_classifier_vug_core_is_not_sec_company_candidate(self):
        clf = classify_sec_metric_candidate("VUG", "Core")
        assert clf["is_sec_company_candidate"] is False
        assert clf["classification"] == "likely_fund_or_etf"
        assert "asset_type_not_sec_company" in clf["blocking_reason_codes"]
        assert "likely_fund_or_etf" in clf["blocking_reason_codes"]

    def test_classifier_vug_empty_category_is_not_sec_company_candidate(self):
        clf = classify_sec_metric_candidate("VUG", "")
        assert clf["is_sec_company_candidate"] is False
        assert clf["classification"] == "likely_fund_or_etf"


# =============================================================================
# AC 2 — VUG miscategorized as Core/Other/empty → Phase 8E: not in selected
# =============================================================================

class TestVugPhase8EExpansion:
    def test_vug_core_not_selected(self):
        selected, skipped = _select(
            positions=[{"ticker": "VUG", "category": "Core"}],
            snapshot_by_ticker={},
        )
        assert "VUG" not in selected

    def test_vug_core_skipped_with_asset_type_reason(self):
        selected, skipped = _select(
            positions=[{"ticker": "VUG", "category": "Core"}],
            snapshot_by_ticker={},
        )
        assert "asset_type_not_sec_company" in skipped
        assert "VUG" in skipped["asset_type_not_sec_company"]

    def test_vug_core_skipped_with_likely_fund_or_etf(self):
        selected, skipped = _select(
            positions=[{"ticker": "VUG", "category": "Core"}],
            snapshot_by_ticker={},
        )
        assert "likely_fund_or_etf" in skipped
        assert "VUG" in skipped["likely_fund_or_etf"]

    def test_vug_other_not_selected(self):
        selected, _ = _select(
            positions=[{"ticker": "VUG", "category": "Other"}],
            snapshot_by_ticker={},
        )
        assert "VUG" not in selected

    def test_vug_empty_category_not_selected(self):
        selected, _ = _select(
            positions=[{"ticker": "VUG", "category": ""}],
            snapshot_by_ticker={},
        )
        assert "VUG" not in selected

    def test_vug_alongside_company_ticker_only_company_selected(self):
        """VUG (Core) present with a real company ticker — only company selected."""
        selected, skipped = _select(
            positions=[
                {"ticker": "VUG", "category": "Core"},
                {"ticker": "AMD", "category": "Core"},
            ],
            snapshot_by_ticker={},
        )
        assert "AMD" in selected
        assert "VUG" not in selected


# =============================================================================
# AC 3 — Existing ETF/Crypto category behavior unchanged (no regression)
# =============================================================================

class TestExistingCategoryBehaviorUnchanged:
    def test_etf_category_still_gets_fund_etf_codes_phase8d(self):
        result = _coverage(
            portfolio_positions=[{"ticker": "SPY", "category": "ETF"}],
            snapshot_by_ticker={},
        )
        codes = result.by_ticker["SPY"]["blocking_reason_codes"]
        assert "asset_type_not_sec_company" in codes
        assert "likely_fund_or_etf" in codes

    def test_crypto_category_still_gets_crypto_codes_phase8d(self):
        result = _coverage(
            portfolio_positions=[{"ticker": "BTC", "category": "Crypto"}],
            snapshot_by_ticker={},
        )
        codes = result.by_ticker["BTC"]["blocking_reason_codes"]
        assert "asset_type_not_sec_company" in codes
        assert "likely_crypto" in codes

    def test_core_category_non_known_ticker_no_asset_type_codes_phase8d(self):
        result = _coverage(
            portfolio_positions=[{"ticker": "AAPL", "category": "Core"}],
            snapshot_by_ticker={},
        )
        codes = result.by_ticker["AAPL"]["blocking_reason_codes"]
        assert "asset_type_not_sec_company" not in codes
        assert "likely_fund_or_etf" not in codes
        assert "likely_crypto" not in codes

    def test_etf_category_ticker_not_selected_phase8e(self):
        selected, skipped = _select(
            positions=[{"ticker": "QQQ", "category": "ETF"}],
            snapshot_by_ticker={},
        )
        assert "QQQ" not in selected
        assert "asset_type_not_sec_company" in skipped

    def test_crypto_category_ticker_not_selected_phase8e(self):
        selected, skipped = _select(
            positions=[{"ticker": "ETH", "category": "Crypto"}],
            snapshot_by_ticker={},
        )
        assert "ETH" not in selected
        assert "asset_type_not_sec_company" in skipped

    def test_classifier_etf_category_returns_fund_or_etf(self):
        clf = classify_sec_metric_candidate("SPY", "ETF")
        assert clf["is_sec_company_candidate"] is False
        assert clf["classification"] == "likely_fund_or_etf"

    def test_classifier_crypto_category_returns_likely_crypto(self):
        clf = classify_sec_metric_candidate("ETH", "Crypto")
        assert clf["is_sec_company_candidate"] is False
        assert clf["classification"] == "likely_crypto"


# =============================================================================
# AC 4 — BTC/XRP classified as crypto-like even with wrong or empty category
# =============================================================================

class TestBtcXrpWrongCategory:
    def test_btc_core_classified_as_crypto_phase8d(self):
        result = _coverage(
            portfolio_positions=[{"ticker": "BTC", "category": "Core"}],
            snapshot_by_ticker={},
        )
        codes = result.by_ticker["BTC"]["blocking_reason_codes"]
        assert "asset_type_not_sec_company" in codes
        assert "likely_crypto" in codes

    def test_xrp_empty_category_classified_as_crypto_phase8d(self):
        result = _coverage(
            portfolio_positions=[{"ticker": "XRP", "category": ""}],
            snapshot_by_ticker={},
        )
        codes = result.by_ticker["XRP"]["blocking_reason_codes"]
        assert "asset_type_not_sec_company" in codes
        assert "likely_crypto" in codes

    def test_btc_other_not_selected_phase8e(self):
        selected, skipped = _select(
            positions=[{"ticker": "BTC", "category": "Other"}],
            snapshot_by_ticker={},
        )
        assert "BTC" not in selected
        assert "asset_type_not_sec_company" in skipped
        assert "BTC" in skipped.get("likely_crypto", [])

    def test_xrp_core_not_selected_phase8e(self):
        selected, skipped = _select(
            positions=[{"ticker": "XRP", "category": "Core"}],
            snapshot_by_ticker={},
        )
        assert "XRP" not in selected

    def test_classifier_btc_core_is_likely_crypto(self):
        clf = classify_sec_metric_candidate("BTC", "Core")
        assert clf["is_sec_company_candidate"] is False
        assert clf["classification"] == "likely_crypto"

    def test_classifier_xrp_empty_is_likely_crypto(self):
        clf = classify_sec_metric_candidate("XRP", "")
        assert clf["is_sec_company_candidate"] is False
        assert clf["classification"] == "likely_crypto"

    def test_btc_xrp_in_known_crypto_set(self):
        assert "BTC" in KNOWN_CRYPTO_TICKERS
        assert "XRP" in KNOWN_CRYPTO_TICKERS

    def test_vug_in_known_fund_or_etf_set(self):
        assert "VUG" in KNOWN_FUND_OR_ETF_TICKERS


# =============================================================================
# AC 5 — Normal company ticker with no snapshot + Core → eligible for Phase 8E
# =============================================================================

class TestCompanyTickerNoSnapshotEligible:
    def test_new_company_ticker_no_snapshot_is_eligible(self):
        selected, skipped = _select(
            positions=[{"ticker": "NEWCO", "category": "Core"}],
            snapshot_by_ticker={},
        )
        assert "NEWCO" in selected

    def test_multiple_eligible_company_tickers_all_selected(self):
        selected, _ = _select(
            positions=[
                {"ticker": "CO1", "category": "Core"},
                {"ticker": "CO2", "category": "Other"},
                {"ticker": "CO3", "category": "Core"},
            ],
            snapshot_by_ticker={},
        )
        assert "CO1" in selected
        assert "CO2" in selected
        assert "CO3" in selected

    def test_classifier_unknown_company_ticker_is_sec_company_candidate(self):
        clf = classify_sec_metric_candidate("NEWCO", "Core")
        assert clf["is_sec_company_candidate"] is True
        assert clf["classification"] == "sec_company_like"
        assert clf["blocking_reason_codes"] == []


# =============================================================================
# AC 6 — Snapshot present + fact_count == 0 → attempted-no-evidence in Phase 8D
# =============================================================================

class TestAttemptedNoEvidencePhase8D:
    def _make_snapshot_present_no_facts(self, ticker: str) -> dict:
        """Snapshot entry for a ticker with artifact but zero source-linked facts."""
        aid = _aid()
        # Artifact exists but no SEC metric facts (empty facts list).
        return _run_phase8b(
            artifact_rows=[_make_artifact(aid, ticker=ticker)],
            facts_by_artifact={aid: []},
        )

    def test_snapshot_present_fact_count_zero_gets_attempted_code(self):
        snapshot = self._make_snapshot_present_no_facts("BLSH")
        result = _coverage(
            portfolio_positions=[{"ticker": "BLSH", "category": "Core"}],
            snapshot_by_ticker=snapshot,
        )
        codes = result.by_ticker["BLSH"]["blocking_reason_codes"]
        assert "attempted_no_source_linked_sec_metric_evidence" in codes, codes

    def test_snapshot_present_fact_count_zero_gets_manual_review_code(self):
        snapshot = self._make_snapshot_present_no_facts("KLAR")
        result = _coverage(
            portfolio_positions=[{"ticker": "KLAR", "category": "Core"}],
            snapshot_by_ticker=snapshot,
        )
        codes = result.by_ticker["KLAR"]["blocking_reason_codes"]
        assert "manual_review_required_before_retry" in codes, codes

    def test_no_snapshot_ticker_does_not_get_attempted_code(self):
        """Ticker with no snapshot at all should NOT get attempted codes."""
        result = _coverage(
            portfolio_positions=[{"ticker": "NEVERRUN", "category": "Core"}],
            snapshot_by_ticker={},
        )
        codes = result.by_ticker["NEVERRUN"]["blocking_reason_codes"]
        assert "attempted_no_source_linked_sec_metric_evidence" not in codes
        assert "manual_review_required_before_retry" not in codes

    def test_snapshot_with_facts_does_not_get_attempted_codes(self):
        """Ticker with snapshot and fact_count > 0 must NOT get attempted codes."""
        aid = _aid()
        snapshot = _run_phase8b(
            artifact_rows=[_make_artifact(aid, ticker="AAPL")],
            facts_by_artifact={aid: _full_facts(aid)},
        )
        result = _coverage(
            portfolio_positions=[{"ticker": "AAPL", "category": "Core"}],
            snapshot_by_ticker=snapshot,
        )
        codes = result.by_ticker["AAPL"]["blocking_reason_codes"]
        assert "attempted_no_source_linked_sec_metric_evidence" not in codes
        assert "manual_review_required_before_retry" not in codes

    def test_etf_ticker_with_snapshot_no_facts_does_not_get_attempted_codes(self):
        """ETF/fund/crypto tickers (non-company) should not get attempted-evidence codes."""
        snapshot = self._make_snapshot_present_no_facts("SPY")
        result = _coverage(
            portfolio_positions=[{"ticker": "SPY", "category": "ETF"}],
            snapshot_by_ticker=snapshot,
        )
        codes = result.by_ticker["SPY"]["blocking_reason_codes"]
        assert "attempted_no_source_linked_sec_metric_evidence" not in codes
        assert "manual_review_required_before_retry" not in codes
        # ETF codes present.
        assert "asset_type_not_sec_company" in codes
        assert "likely_fund_or_etf" in codes

    def test_always_blocking_codes_still_present_with_attempted_codes(self):
        snapshot = self._make_snapshot_present_no_facts("TSM")
        result = _coverage(
            portfolio_positions=[{"ticker": "TSM", "category": "Core"}],
            snapshot_by_ticker=snapshot,
        )
        codes = result.by_ticker["TSM"]["blocking_reason_codes"]
        assert "decision_consumption_disabled" in codes
        assert "safe_for_decision_db_lock" in codes
        assert "attempted_no_source_linked_sec_metric_evidence" in codes
        assert "manual_review_required_before_retry" in codes

    def test_attempted_codes_sorted_with_other_codes(self):
        snapshot = self._make_snapshot_present_no_facts("BLSH")
        result = _coverage(
            portfolio_positions=[{"ticker": "BLSH", "category": "Core"}],
            snapshot_by_ticker=snapshot,
        )
        codes = result.by_ticker["BLSH"]["blocking_reason_codes"]
        assert codes == sorted(codes)


# =============================================================================
# AC 7 — BLSH/KLAR/TSM-style: skipped by Phase 8E with attempted/no-evidence
# =============================================================================

class TestAttemptedNoEvidencePhase8ESkip:
    def _make_snapshot_no_facts(self, ticker: str) -> dict:
        aid = _aid()
        return _run_phase8b(
            artifact_rows=[_make_artifact(aid, ticker=ticker)],
            facts_by_artifact={aid: []},
        )

    def test_blsh_style_not_selected(self):
        snapshot = self._make_snapshot_no_facts("BLSH")
        selected, _ = _select(
            positions=[{"ticker": "BLSH", "category": "Core"}],
            snapshot_by_ticker=snapshot,
        )
        assert "BLSH" not in selected

    def test_blsh_style_skipped_with_attempted_reason(self):
        snapshot = self._make_snapshot_no_facts("BLSH")
        selected, skipped = _select(
            positions=[{"ticker": "BLSH", "category": "Core"}],
            snapshot_by_ticker=snapshot,
        )
        assert "attempted_no_source_linked_sec_metric_evidence" in skipped
        assert "BLSH" in skipped["attempted_no_source_linked_sec_metric_evidence"]

    def test_blsh_style_skipped_with_manual_review_reason(self):
        snapshot = self._make_snapshot_no_facts("BLSH")
        selected, skipped = _select(
            positions=[{"ticker": "BLSH", "category": "Core"}],
            snapshot_by_ticker=snapshot,
        )
        assert "manual_review_required_before_retry" in skipped
        assert "BLSH" in skipped["manual_review_required_before_retry"]

    def test_klar_style_not_selected(self):
        snapshot = self._make_snapshot_no_facts("KLAR")
        selected, _ = _select(
            positions=[{"ticker": "KLAR", "category": "Core"}],
            snapshot_by_ticker=snapshot,
        )
        assert "KLAR" not in selected

    def test_tsm_style_not_selected(self):
        snapshot = self._make_snapshot_no_facts("TSM")
        selected, _ = _select(
            positions=[{"ticker": "TSM", "category": "Core"}],
            snapshot_by_ticker=snapshot,
        )
        assert "TSM" not in selected

    def test_blsh_not_selected_alongside_eligible_company(self):
        """BLSH (attempted/no-evidence) alongside a new company — only new company selected."""
        blsh_snap = self._make_snapshot_no_facts("BLSH")
        selected, skipped = _select(
            positions=[
                {"ticker": "BLSH", "category": "Core"},
                {"ticker": "NEWCO", "category": "Core"},
            ],
            snapshot_by_ticker=blsh_snap,
        )
        assert "NEWCO" in selected
        assert "BLSH" not in selected

    def test_no_snapshot_ticker_not_skipped_as_attempted(self):
        """A ticker with no snapshot at all (never run) stays eligible."""
        selected, skipped = _select(
            positions=[{"ticker": "NEVERRUN", "category": "Core"}],
            snapshot_by_ticker={},
        )
        assert "NEVERRUN" in selected
        assert "attempted_no_source_linked_sec_metric_evidence" not in skipped


# =============================================================================
# AC 8 — READY/PARTIAL evidence-producing tickers not reclassified
# =============================================================================

class TestExistingReadyPartialNotReclassified:
    def test_ready_ticker_not_reclassified_phase8d(self):
        aid = _aid()
        snapshot = _run_phase8b(
            artifact_rows=[_make_artifact(aid, ticker="AAPL")],
            facts_by_artifact={aid: _full_facts(aid)},
        )
        result = _coverage(
            portfolio_positions=[{"ticker": "AAPL", "category": "Core"}],
            snapshot_by_ticker=snapshot,
        )
        assert result.by_ticker["AAPL"]["future_adapter_readiness"] == "READY_DRY_RUN_ONLY"
        assert result.by_ticker["AAPL"]["has_sec_research_artifacts"] is True
        codes = result.by_ticker["AAPL"]["blocking_reason_codes"]
        assert "asset_type_not_sec_company" not in codes
        assert "attempted_no_source_linked_sec_metric_evidence" not in codes

    def test_partial_ticker_not_reclassified_phase8d(self):
        from app.services.intelligence.research_workers.sec_metric_truth_adapter_dry_run import (
            SEC_METRIC_BUCKET_MAP,
        )
        aid = _aid()
        capex_tags = {t for t, b in SEC_METRIC_BUCKET_MAP.items() if b == "capex"}
        non_capex_facts = [
            _make_metric_fact(aid, tag=t)
            for t in _ALL_TAGS
            if t not in capex_tags
        ]
        snapshot = _run_phase8b(
            artifact_rows=[_make_artifact(aid, ticker="NVDA")],
            facts_by_artifact={aid: non_capex_facts},
        )
        result = _coverage(
            portfolio_positions=[{"ticker": "NVDA", "category": "Core"}],
            snapshot_by_ticker=snapshot,
        )
        assert result.by_ticker["NVDA"]["future_adapter_readiness"] == "PARTIAL_DRY_RUN_ONLY"
        codes = result.by_ticker["NVDA"]["blocking_reason_codes"]
        assert "attempted_no_source_linked_sec_metric_evidence" not in codes

    def test_ready_ticker_not_selected_phase8e(self):
        aid = _aid()
        snapshot = _run_phase8b(
            artifact_rows=[_make_artifact(aid, ticker="AAPL")],
            facts_by_artifact={aid: _full_facts(aid)},
        )
        selected, skipped = _select(
            positions=[{"ticker": "AAPL", "category": "Core"}],
            snapshot_by_ticker=snapshot,
        )
        assert "AAPL" not in selected
        assert "AAPL" in skipped.get("already_has_sec_metric_evidence", [])

    def test_ready_ticker_skipped_with_evidence_not_attempted_reason_phase8e(self):
        """READY ticker must be skipped with 'already_has_sec_metric_evidence', not attempted codes."""
        aid = _aid()
        snapshot = _run_phase8b(
            artifact_rows=[_make_artifact(aid, ticker="MSFT")],
            facts_by_artifact={aid: _full_facts(aid)},
        )
        selected, skipped = _select(
            positions=[{"ticker": "MSFT", "category": "Core"}],
            snapshot_by_ticker=snapshot,
        )
        assert "attempted_no_source_linked_sec_metric_evidence" not in skipped or \
               "MSFT" not in skipped.get("attempted_no_source_linked_sec_metric_evidence", [])


# =============================================================================
# AC 9 — All relevant outputs deterministic and sorted
# =============================================================================

class TestDeterministicAndSorted:
    def test_blocking_codes_sorted_with_attempted_codes(self):
        aid = _aid()
        snapshot = _run_phase8b(
            artifact_rows=[_make_artifact(aid, ticker="BLSH")],
            facts_by_artifact={aid: []},
        )
        result = _coverage(
            portfolio_positions=[{"ticker": "BLSH", "category": "Core"}],
            snapshot_by_ticker=snapshot,
        )
        codes = result.by_ticker["BLSH"]["blocking_reason_codes"]
        assert codes == sorted(codes)

    def test_blocking_codes_sorted_for_vug_core(self):
        result = _coverage(
            portfolio_positions=[{"ticker": "VUG", "category": "Core"}],
            snapshot_by_ticker={},
        )
        codes = result.by_ticker["VUG"]["blocking_reason_codes"]
        assert codes == sorted(codes)

    def test_skipped_ticker_lists_sorted_in_phase8e(self):
        aid_blsh = _aid()
        aid_klar = _aid()
        snapshot = {
            **_run_phase8b(
                artifact_rows=[_make_artifact(aid_blsh, ticker="BLSH")],
                facts_by_artifact={aid_blsh: []},
            ),
            **_run_phase8b(
                artifact_rows=[_make_artifact(aid_klar, ticker="KLAR")],
                facts_by_artifact={aid_klar: []},
            ),
        }
        selected, skipped = _select(
            positions=[
                {"ticker": "KLAR", "category": "Core"},
                {"ticker": "BLSH", "category": "Core"},
            ],
            snapshot_by_ticker=snapshot,
        )
        for reason, tickers in skipped.items():
            assert tickers == sorted(tickers), f"Not sorted for reason={reason}"

    def test_two_runs_identical_result_for_same_input(self):
        aid = _aid()
        snapshot = _run_phase8b(
            artifact_rows=[_make_artifact(aid, ticker="BLSH")],
            facts_by_artifact={aid: []},
        )
        r1 = _coverage(
            portfolio_positions=[{"ticker": "BLSH", "category": "Core"}],
            snapshot_by_ticker=snapshot,
        )
        r2 = _coverage(
            portfolio_positions=[{"ticker": "BLSH", "category": "Core"}],
            snapshot_by_ticker=snapshot,
        )
        assert r1.by_ticker == r2.by_ticker


# =============================================================================
# AC 10 — Static import guard: no forbidden imports in 8D/8E/classifier modules
# =============================================================================

_FORBIDDEN_IMPORTS = {
    "decide",
    "decision_policy_v1",
    "IntelV3Service",
    "recommendation_engine",
    "intel_v3_service",
}


def _check_no_forbidden_imports(src: str) -> None:
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module = getattr(node, "module", "") or ""
            names = [alias.name for alias in node.names]
            for part in [module] + names:
                for forbidden in _FORBIDDEN_IMPORTS:
                    assert forbidden not in part, (
                        f"Forbidden import '{forbidden}' found in module: ...{part}..."
                    )


class TestStaticImportGuardsPhase8F:
    def test_no_forbidden_imports_in_classifier(self):
        src = _read_module_src("sec_metric_candidate_classifier.py")
        _check_no_forbidden_imports(src)

    def test_no_forbidden_imports_in_phase8d(self):
        src = _read_module_src("sec_metric_portfolio_coverage_dry_run.py")
        _check_no_forbidden_imports(src)

    def test_no_forbidden_imports_in_phase8e(self):
        src = _read_module_src("sec_metric_coverage_expansion.py")
        _check_no_forbidden_imports(src)

    def test_no_safe_for_decision_true_in_classifier(self):
        src = _read_module_src("sec_metric_candidate_classifier.py")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword):
                if (
                    node.arg == "safe_for_decision"
                    and isinstance(node.value, ast.Constant)
                    and node.value.value is True
                ):
                    raise AssertionError("safe_for_decision=True found in classifier")

    def test_no_db_writes_in_classifier(self):
        src = _read_module_src("sec_metric_candidate_classifier.py")
        assert ".insert(" not in src
        assert ".upsert(" not in src
        assert ".delete(" not in src


# =============================================================================
# AC 11 — safe_for_decision remains False
# =============================================================================

class TestSafeForDecisionFalse:
    def test_safe_for_decision_false_with_vug_in_portfolio(self):
        result = _coverage(
            portfolio_positions=[{"ticker": "VUG", "category": "Core"}],
            snapshot_by_ticker={},
        )
        assert result.safe_for_decision is False

    def test_safe_for_decision_false_with_blsh_attempted_no_evidence(self):
        aid = _aid()
        snapshot = _run_phase8b(
            artifact_rows=[_make_artifact(aid, ticker="BLSH")],
            facts_by_artifact={aid: []},
        )
        result = _coverage(
            portfolio_positions=[{"ticker": "BLSH", "category": "Core"}],
            snapshot_by_ticker=snapshot,
        )
        assert result.safe_for_decision is False

    def test_safe_for_decision_false_mixed_portfolio(self):
        aid = _aid()
        snapshot = _run_phase8b(
            artifact_rows=[_make_artifact(aid, ticker="AAPL")],
            facts_by_artifact={aid: _full_facts(aid)},
        )
        result = _coverage(
            portfolio_positions=[
                {"ticker": "AAPL", "category": "Core"},
                {"ticker": "VUG", "category": "Core"},
                {"ticker": "BLSH", "category": "Core"},
            ],
            snapshot_by_ticker=snapshot,
        )
        assert result.safe_for_decision is False


# =============================================================================
# AC 12 — visible_snapshot_unchanged remains True
# =============================================================================

class TestVisibleSnapshotUnchanged:
    def test_visible_snapshot_unchanged_true_with_vug(self):
        result = _coverage(
            portfolio_positions=[{"ticker": "VUG", "category": "Core"}],
            snapshot_by_ticker={},
        )
        assert result.visible_snapshot_unchanged is True

    def test_visible_snapshot_unchanged_true_with_attempted_no_evidence(self):
        aid = _aid()
        snapshot = _run_phase8b(
            artifact_rows=[_make_artifact(aid, ticker="BLSH")],
            facts_by_artifact={aid: []},
        )
        result = _coverage(
            portfolio_positions=[{"ticker": "BLSH", "category": "Core"}],
            snapshot_by_ticker=snapshot,
        )
        assert result.visible_snapshot_unchanged is True


# =============================================================================
# AC 13 — No raw metric values, structured_payload, source URLs, excerpts, rows
# =============================================================================

class TestNoRawDataExposed:
    def test_by_ticker_no_raw_fields_for_vug(self):
        result = _coverage(
            portfolio_positions=[{"ticker": "VUG", "category": "Core"}],
            snapshot_by_ticker={},
        )
        ticker_dict = result.by_ticker["VUG"]
        for forbidden_key in [
            "structured_payload", "raw_metric_values", "source_url",
            "source_excerpt", "data", "rows",
        ]:
            assert forbidden_key not in ticker_dict

    def test_by_ticker_no_raw_fields_for_blsh_attempted(self):
        aid = _aid()
        snapshot = _run_phase8b(
            artifact_rows=[_make_artifact(aid, ticker="BLSH")],
            facts_by_artifact={aid: []},
        )
        result = _coverage(
            portfolio_positions=[{"ticker": "BLSH", "category": "Core"}],
            snapshot_by_ticker=snapshot,
        )
        ticker_dict = result.by_ticker["BLSH"]
        for forbidden_key in [
            "structured_payload", "raw_metric_values", "source_url",
            "source_excerpt", "data", "rows",
        ]:
            assert forbidden_key not in ticker_dict

    def test_result_has_no_raw_fields(self):
        result = _coverage(
            portfolio_positions=[{"ticker": "VUG", "category": "Core"}],
            snapshot_by_ticker={},
        )
        assert not hasattr(result, "structured_payload")
        assert not hasattr(result, "raw_metric_values")
        assert not hasattr(result, "source_url")

    def test_fact_count_is_integer_not_raw_value(self):
        aid = _aid()
        snapshot = _run_phase8b(
            artifact_rows=[_make_artifact(aid, ticker="BLSH")],
            facts_by_artifact={aid: []},
        )
        result = _coverage(
            portfolio_positions=[{"ticker": "BLSH", "category": "Core"}],
            snapshot_by_ticker=snapshot,
        )
        fc = result.by_ticker["BLSH"]["source_linked_metric_fact_count"]
        assert isinstance(fc, int)
        # Raw fixture value 123456789 must never appear as the count.
        assert fc != 123456789
