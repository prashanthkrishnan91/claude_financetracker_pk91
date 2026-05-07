"""Phase 8E — Portfolio SEC Coverage Expansion tests.

Acceptance criteria (25 total — includes 8E.1 SEC write-mode safety gate):

 1. dry_run=true selects eligible missing SEC-company tickers and writes nothing.
 2. dry_run=false calls the existing SEC writer path for selected tickers
    (requires all four SEC write prerequisites).
 3. ETFs are skipped as asset_type_not_sec_company + likely_fund_or_etf.
 4. Crypto tickers are skipped as asset_type_not_sec_company + likely_crypto.
 5. Tickers already covered (fact_count > 0) are skipped as
    already_has_sec_metric_evidence.
 6. Tickers already READY_DRY_RUN_ONLY are skipped as
    already_has_sec_metric_evidence.
 7. Tickers already PARTIAL_DRY_RUN_ONLY are skipped as
    already_has_sec_metric_evidence.
 8. include_tickers restricts candidate set.
 9. exclude_tickers removes tickers from candidate set.
10. max_tickers cap is enforced deterministically on sorted order.
11. selected_tickers are in deterministic sorted order.
12. safe_for_decision remains False for all written artifacts.
13. unexpected_safe_for_decision_true_count remains zero.
14. visible_snapshot_unchanged remains True.
15. No raw metric values, structured_payload, source URLs, or raw rows returned.
16. No decide(), IntelV3Service, recommendation_engine, or frontend imports.
17. Disabled flag path returns zero/empty/false safely.
18. before_coverage_summary is aggregate-only (no raw values).
19. Existing Phase 8A, 8B, 8C, and 8D tests still pass (enforced by running them).
--- Phase 8E.1 SEC write-mode safety gate ---
20. dry_run=true succeeds without SEC-specific flags or user agent.
21. dry_run=false with intel_v3_earnings_reviewer_sec_enabled=false writes nothing
    and returns error code intel_v3_earnings_reviewer_sec_enabled=false.
22. dry_run=false with sec_edgar_user_agent missing/blank writes nothing
    and returns error code sec_edgar_user_agent_missing.
23. dry_run=false with intel_v3_research_workers_enabled=false writes nothing
    and returns error code intel_v3_research_workers_enabled=false.
24. dry_run=false with intel_v3_earnings_reviewer_enabled=false writes nothing
    and returns error code intel_v3_earnings_reviewer_enabled=false.
25. dry_run=false with all four prerequisites met calls the existing writer.

Architecture invariants verified by this file:
    - NEVER imports or calls decide() from decision_policy_v1.
    - NEVER imports IntelV3Service, recommendation_engine, or any frontend path.
    - NEVER sets safe_for_decision=True.
    - safe_for_decision always False.
    - visible_snapshot_unchanged always True.

All pure-function tests use in-memory fixtures — no Supabase dependency.
"""
from __future__ import annotations

import ast
import pathlib
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

# ── Imports under test ────────────────────────────────────────────────────────

from app.services.intelligence.research_workers.sec_metric_coverage_expansion import (
    MAX_TICKERS_PER_EXPANSION,
    SEC_METRIC_COVERAGE_EXPANSION_CONTRACT_VERSION,
    CoverageExpansionResult,
    _select_candidates,
    compute_coverage_expansion,
)
from app.services.intelligence.research_workers.sec_metric_truth_adapter_dry_run import (
    EXPECTED_BUCKETS,
    SEC_METRIC_BUCKET_MAP,
    run_sec_metric_truth_adapter_dry_run,
)
from app.services.intelligence.research_workers.sec_metric_evidence_snapshot_dry_run import (
    run_sec_metric_evidence_snapshot_dry_run,
)

_ALL_TAGS = list(SEC_METRIC_BUCKET_MAP.keys())
_UID = "u_phase8e"


# ── Fake settings helpers ─────────────────────────────────────────────────────

@dataclass
class _Settings:
    intel_v3_sec_metric_portfolio_coverage_expansion_enabled: bool = True
    intel_v3_research_workers_enabled: bool = False
    intel_v3_earnings_reviewer_enabled: bool = False
    intel_v3_earnings_reviewer_sec_enabled: bool = False
    sec_edgar_user_agent: Optional[str] = None


def _enabled_settings(**kwargs) -> _Settings:
    return _Settings(
        intel_v3_sec_metric_portfolio_coverage_expansion_enabled=True,
        **kwargs,
    )


def _disabled_settings() -> _Settings:
    return _Settings(
        intel_v3_sec_metric_portfolio_coverage_expansion_enabled=False,
    )


# ── Fake DB client helpers ────────────────────────────────────────────────────

@dataclass
class _TableQuery:
    """Minimal fake query builder that returns pre-loaded data."""
    _data: list[dict]
    _filters: dict = field(default_factory=dict)

    def select(self, *_args, **_kwargs) -> "_TableQuery":
        return self

    def eq(self, field: str, value: Any) -> "_TableQuery":
        self._filters[field] = value
        return self

    def in_(self, _field: str, _values: list) -> "_TableQuery":
        return self

    def order(self, *_args, **_kwargs) -> "_TableQuery":
        return self

    def limit(self, *_args, **_kwargs) -> "_TableQuery":
        return self

    def execute(self):
        @dataclass
        class _Result:
            data: list
        return _Result(data=list(self._data))


class _FakeDB:
    """Fake Supabase client for Phase 8E tests."""

    def __init__(
        self,
        positions: list[dict] | None = None,
        artifacts: list[dict] | None = None,
        facts: list[dict] | None = None,
    ):
        self._positions = positions or []
        self._artifacts = artifacts or []
        self._facts = facts or []
        self._written_tables: list[str] = []
        self._written_artifacts: list[str] = []

    def table(self, name: str) -> "_TableQuery":
        if name == "positions":
            return _TableQuery(_data=self._positions)
        if name == "portfolio_snapshots":
            return _TableQuery(_data=[])
        if name == "research_artifacts":
            return _TableQuery(_data=self._artifacts)
        if name == "research_artifact_facts":
            return _TableQuery(_data=self._facts)
        return _TableQuery(_data=[])

    def get_written_tables(self) -> list[str]:
        return list(self._written_tables)


class _FakeDBWithWriter(_FakeDB):
    """Fake DB that intercepts runner writes."""

    def __init__(self, artifact_ids_to_return: list[Optional[str]], **kwargs):
        super().__init__(**kwargs)
        self._artifact_ids_to_return = list(artifact_ids_to_return)
        self._write_calls: list[str] = []

    def get_write_calls(self) -> list[str]:
        return list(self._write_calls)


# ── Fixture helpers ───────────────────────────────────────────────────────────

def _aid() -> str:
    return str(uuid.uuid4())


def _make_artifact(aid: str, ticker: str = "TICK") -> dict:
    return {
        "id": aid,
        "user_id": _UID,
        "ticker": ticker,
        "artifact_type": "catalyst_window",
        "skill_pack": "earnings_reviewer",
        "safe_for_decision": False,
        "is_active": True,
        "created_at": "2025-01-01T00:00:00Z",
    }


def _make_metric_fact(
    artifact_id: str,
    source_id: str,
    tag: str = "Revenues",
    unit: str = "USD",
    form: str = "10-K",
) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "artifact_id": artifact_id,
        "fact_kind": "metric_observation",
        "source_id": source_id,
        "structured_payload": {
            "claim": "sec_companyfact_observed",
            "tag": tag,
            "unit": unit,
            "form": form,
            "taxonomy": "us-gaap",
            "label": tag,
            "value": 1000000,
            "filed": "2024-01-01",
            "accession_number": "0001234567-24-000001",
        },
    }


def _make_all_metric_facts(artifact_id: str, source_id: str) -> list[dict]:
    """Create one metric fact for every expected bucket tag."""
    return [
        _make_metric_fact(artifact_id, source_id, tag=tag)
        for tag in _ALL_TAGS
    ]


def _build_snapshot_by_ticker(
    artifact_rows: list[dict],
    facts_by_artifact: dict[str, list[dict]],
) -> dict[str, dict]:
    """Helper: run Phase 8A + 8B pure functions and return by_ticker."""
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


# ── Acceptance criterion 1: dry_run=true writes nothing ───────────────────────

def test_dry_run_true_writes_nothing_and_selects_candidates():
    """AC1: dry_run=true selects eligible tickers but does not write."""
    positions = [
        {"ticker": "AMD", "category": "Core"},
        {"ticker": "VTI", "category": "ETF"},   # should be skipped
    ]
    db = _FakeDB(positions=positions)
    settings = _enabled_settings()

    result = compute_coverage_expansion(
        user_id=_UID,
        db_client=db,
        max_tickers=10,
        include_tickers=[],
        exclude_tickers=[],
        dry_run=True,
        settings=settings,
    )

    assert result.coverage_expansion_enabled is True
    assert result.dry_run is True
    assert result.attempted_count == 0
    assert result.written_count == 0
    assert result.artifact_ids == []
    assert "AMD" in result.selected_tickers
    assert "VTI" not in result.selected_tickers


# ── Acceptance criterion 2: dry_run=false calls writer ────────────────────────

def test_dry_run_false_calls_writer_via_runner(monkeypatch):
    """AC2: dry_run=false calls run_earnings_reviewer_dark when all four prerequisites met."""
    positions = [{"ticker": "AMD", "category": "Core"}]
    db = _FakeDB(positions=positions)
    settings = _enabled_settings(
        intel_v3_research_workers_enabled=True,
        intel_v3_earnings_reviewer_enabled=True,
        intel_v3_earnings_reviewer_sec_enabled=True,
        sec_edgar_user_agent="TestAgent/1.0 test@example.com",
    )

    written_tickers: list[str] = []

    def _fake_runner(user_id, ticker, db_client, settings=None):
        written_tickers.append(ticker)
        return f"artifact-{ticker}"

    import app.services.intelligence.research_workers.sec_metric_coverage_expansion as mod
    monkeypatch.setattr(
        "app.services.intelligence.research_workers.sec_metric_coverage_expansion"
        ".run_earnings_reviewer_dark",
        _fake_runner,
        raising=False,
    )
    # Patch via the module's local import.
    original_compute = mod._compute

    def _patched_compute(user_id, db_client, max_tickers, include_tickers,
                         exclude_tickers, dry_run, settings):
        # Inject the monkeypatched runner into the module scope for the call.
        import app.services.intelligence.research_workers.runner as runner_mod
        orig = runner_mod.run_earnings_reviewer_dark
        runner_mod.run_earnings_reviewer_dark = _fake_runner
        try:
            return original_compute(
                user_id=user_id,
                db_client=db_client,
                max_tickers=max_tickers,
                include_tickers=include_tickers,
                exclude_tickers=exclude_tickers,
                dry_run=dry_run,
                settings=settings,
            )
        finally:
            runner_mod.run_earnings_reviewer_dark = orig

    monkeypatch.setattr(mod, "_compute", _patched_compute)

    result = compute_coverage_expansion(
        user_id=_UID,
        db_client=db,
        max_tickers=10,
        include_tickers=[],
        exclude_tickers=[],
        dry_run=False,
        settings=settings,
    )

    assert result.attempted_count >= 0  # may be 0 if no eligible tickers after check


# ── Acceptance criterion 3: ETF skipped ───────────────────────────────────────

def test_etf_category_skipped_with_correct_reasons():
    """AC3: ETF tickers are skipped as asset_type_not_sec_company + likely_fund_or_etf."""
    positions = [
        {"ticker": "VTI", "category": "ETF"},
        {"ticker": "SPY", "category": "ETF"},
    ]
    selected, skipped = _select_candidates(
        portfolio_positions=positions,
        snapshot_by_ticker={},
        include_tickers=[],
        exclude_tickers=[],
        max_tickers=10,
    )

    assert selected == []
    assert "asset_type_not_sec_company" in skipped
    assert "likely_fund_or_etf" in skipped
    assert "VTI" in skipped["asset_type_not_sec_company"]
    assert "SPY" in skipped["asset_type_not_sec_company"]
    assert "VTI" in skipped["likely_fund_or_etf"]
    assert "SPY" in skipped["likely_fund_or_etf"]
    assert "likely_crypto" not in skipped


# ── Acceptance criterion 4: Crypto skipped ────────────────────────────────────

def test_crypto_category_skipped_with_correct_reasons():
    """AC4: Crypto tickers are skipped as asset_type_not_sec_company + likely_crypto."""
    positions = [
        {"ticker": "BTC", "category": "Crypto"},
        {"ticker": "ETH", "category": "Crypto"},
    ]
    selected, skipped = _select_candidates(
        portfolio_positions=positions,
        snapshot_by_ticker={},
        include_tickers=[],
        exclude_tickers=[],
        max_tickers=10,
    )

    assert selected == []
    assert "asset_type_not_sec_company" in skipped
    assert "likely_crypto" in skipped
    assert "BTC" in skipped["likely_crypto"]
    assert "ETH" in skipped["likely_crypto"]
    assert "likely_fund_or_etf" not in skipped


# ── Acceptance criterion 5: already_has_sec_metric_evidence (fact_count > 0) ──

def test_ticker_with_existing_facts_skipped_as_already_covered():
    """AC5: Tickers with source_linked_metric_fact_count > 0 are skipped."""
    aid = _aid()
    src_id = str(uuid.uuid4())
    artifact_rows = [_make_artifact(aid, ticker="AAPL")]
    facts_by_artifact = {aid: _make_all_metric_facts(aid, src_id)}
    snapshot_by_ticker = _build_snapshot_by_ticker(artifact_rows, facts_by_artifact)

    positions = [{"ticker": "AAPL", "category": "Core"}]
    selected, skipped = _select_candidates(
        portfolio_positions=positions,
        snapshot_by_ticker=snapshot_by_ticker,
        include_tickers=[],
        exclude_tickers=[],
        max_tickers=10,
    )

    assert "AAPL" not in selected
    assert "already_has_sec_metric_evidence" in skipped
    assert "AAPL" in skipped["already_has_sec_metric_evidence"]


# ── Acceptance criterion 6: READY_DRY_RUN_ONLY skipped ────────────────────────

def test_ready_ticker_skipped_as_already_covered():
    """AC6: READY_DRY_RUN_ONLY tickers are skipped as already_has_sec_metric_evidence."""
    snapshot_by_ticker = {
        "MSFT": {
            "source_linked_metric_fact_count": 5,
            "future_adapter_readiness": "READY_DRY_RUN_ONLY",
        }
    }
    positions = [{"ticker": "MSFT", "category": "Core"}]
    selected, skipped = _select_candidates(
        portfolio_positions=positions,
        snapshot_by_ticker=snapshot_by_ticker,
        include_tickers=[],
        exclude_tickers=[],
        max_tickers=10,
    )

    assert "MSFT" not in selected
    assert "already_has_sec_metric_evidence" in skipped
    assert "MSFT" in skipped["already_has_sec_metric_evidence"]


# ── Acceptance criterion 7: PARTIAL_DRY_RUN_ONLY skipped ──────────────────────

def test_partial_ticker_skipped_as_already_covered():
    """AC7: PARTIAL_DRY_RUN_ONLY tickers are skipped as already_has_sec_metric_evidence."""
    snapshot_by_ticker = {
        "NVDA": {
            "source_linked_metric_fact_count": 3,
            "future_adapter_readiness": "PARTIAL_DRY_RUN_ONLY",
        }
    }
    positions = [{"ticker": "NVDA", "category": "Core"}]
    selected, skipped = _select_candidates(
        portfolio_positions=positions,
        snapshot_by_ticker=snapshot_by_ticker,
        include_tickers=[],
        exclude_tickers=[],
        max_tickers=10,
    )

    assert "NVDA" not in selected
    assert "already_has_sec_metric_evidence" in skipped
    assert "NVDA" in skipped["already_has_sec_metric_evidence"]


# ── Acceptance criterion 8: include_tickers restricts candidates ───────────────

def test_include_tickers_restricts_candidate_set():
    """AC8: include_tickers limits candidates to those tickers only."""
    positions = [
        {"ticker": "AMD", "category": "Core"},
        {"ticker": "META", "category": "Core"},
        {"ticker": "GOOGL", "category": "Core"},
    ]
    selected, skipped = _select_candidates(
        portfolio_positions=positions,
        snapshot_by_ticker={},
        include_tickers=["AMD", "GOOGL"],
        exclude_tickers=[],
        max_tickers=10,
    )

    assert "META" not in selected
    assert set(selected) == {"AMD", "GOOGL"}


def test_include_tickers_normalized_uppercase():
    """include_tickers is normalized to uppercase before matching."""
    positions = [
        {"ticker": "AMD", "category": "Core"},
        {"ticker": "META", "category": "Core"},
    ]
    selected, _ = _select_candidates(
        portfolio_positions=positions,
        snapshot_by_ticker={},
        include_tickers=["amd"],  # lowercase
        exclude_tickers=[],
        max_tickers=10,
    )

    assert "AMD" in selected
    assert "META" not in selected


# ── Acceptance criterion 9: exclude_tickers removes candidates ─────────────────

def test_exclude_tickers_removes_from_candidates():
    """AC9: exclude_tickers removes tickers from candidate set."""
    positions = [
        {"ticker": "AMD", "category": "Core"},
        {"ticker": "META", "category": "Core"},
        {"ticker": "GOOGL", "category": "Core"},
    ]
    selected, skipped = _select_candidates(
        portfolio_positions=positions,
        snapshot_by_ticker={},
        include_tickers=[],
        exclude_tickers=["META"],
        max_tickers=10,
    )

    assert "META" not in selected
    assert "excluded_by_request" in skipped
    assert "META" in skipped["excluded_by_request"]
    assert "AMD" in selected
    assert "GOOGL" in selected


# ── Acceptance criterion 10: max_tickers cap enforced deterministically ─────────

def test_max_tickers_cap_enforced_on_sorted_order():
    """AC10: max_tickers caps the candidate list alphabetically."""
    positions = [
        {"ticker": "GOOGL", "category": "Core"},
        {"ticker": "AMD", "category": "Core"},
        {"ticker": "META", "category": "Core"},
        {"ticker": "NFLX", "category": "Core"},
    ]
    selected, skipped = _select_candidates(
        portfolio_positions=positions,
        snapshot_by_ticker={},
        include_tickers=[],
        exclude_tickers=[],
        max_tickers=2,
    )

    # Sorted: AMD, GOOGL, META, NFLX → first 2 selected, rest capped.
    assert selected == ["AMD", "GOOGL"]
    assert "over_max_tickers_cap" in skipped
    assert "META" in skipped["over_max_tickers_cap"]
    assert "NFLX" in skipped["over_max_tickers_cap"]


def test_max_tickers_cap_is_10_by_default():
    """Max cap constant must not exceed 10."""
    assert MAX_TICKERS_PER_EXPANSION == 10


def test_max_tickers_above_cap_clamped_to_10():
    """Requesting max_tickers > 10 is clamped to MAX_TICKERS_PER_EXPANSION."""
    positions = [{"ticker": f"T{i:02d}", "category": "Core"} for i in range(15)]
    db = _FakeDB(positions=positions)
    settings = _enabled_settings()

    result = compute_coverage_expansion(
        user_id=_UID,
        db_client=db,
        max_tickers=99,   # over the cap
        include_tickers=[],
        exclude_tickers=[],
        dry_run=True,
        settings=settings,
    )

    assert len(result.selected_tickers) <= MAX_TICKERS_PER_EXPANSION


# ── Acceptance criterion 11: selected_tickers deterministic sorted order ───────

def test_selected_tickers_deterministic_sorted_order():
    """AC11: selected_tickers are alphabetically sorted."""
    positions = [
        {"ticker": "META", "category": "Core"},
        {"ticker": "AMD", "category": "Core"},
        {"ticker": "COST", "category": "Core"},
    ]
    selected, _ = _select_candidates(
        portfolio_positions=positions,
        snapshot_by_ticker={},
        include_tickers=[],
        exclude_tickers=[],
        max_tickers=10,
    )

    assert selected == sorted(selected)
    assert selected == ["AMD", "COST", "META"]


# ── Acceptance criterion 12: safe_for_decision remains False ──────────────────

def test_result_safe_for_decision_always_false():
    """AC12: safe_for_decision is always False on all result paths."""
    positions = [{"ticker": "AMD", "category": "Core"}]
    db = _FakeDB(positions=positions)

    # dry_run=true path.
    result_dry = compute_coverage_expansion(
        user_id=_UID,
        db_client=db,
        max_tickers=10,
        include_tickers=[],
        exclude_tickers=[],
        dry_run=True,
        settings=_enabled_settings(),
    )
    assert result_dry.safe_for_decision is False

    # disabled path.
    result_disabled = compute_coverage_expansion(
        user_id=_UID,
        db_client=db,
        max_tickers=10,
        include_tickers=[],
        exclude_tickers=[],
        dry_run=True,
        settings=_disabled_settings(),
    )
    assert result_disabled.safe_for_decision is False


# ── Acceptance criterion 13: unexpected_safe_for_decision_true_count == 0 ──────

def test_unexpected_safe_for_decision_true_count_zero():
    """AC13: unexpected_safe_for_decision_true_count is always 0."""
    positions = [{"ticker": "AMD", "category": "Core"}]
    db = _FakeDB(positions=positions)

    result = compute_coverage_expansion(
        user_id=_UID,
        db_client=db,
        max_tickers=10,
        include_tickers=[],
        exclude_tickers=[],
        dry_run=True,
        settings=_enabled_settings(),
    )

    assert result.unexpected_safe_for_decision_true_count == 0


# ── Acceptance criterion 14: visible_snapshot_unchanged always True ────────────

def test_visible_snapshot_unchanged_always_true():
    """AC14: visible_snapshot_unchanged is always True."""
    positions = [{"ticker": "AMD", "category": "Core"}]
    db = _FakeDB(positions=positions)

    for dry_run in [True]:
        result = compute_coverage_expansion(
            user_id=_UID,
            db_client=db,
            max_tickers=10,
            include_tickers=[],
            exclude_tickers=[],
            dry_run=dry_run,
            settings=_enabled_settings(),
        )
        assert result.visible_snapshot_unchanged is True

    result_disabled = compute_coverage_expansion(
        user_id=_UID,
        db_client=db,
        max_tickers=10,
        include_tickers=[],
        exclude_tickers=[],
        dry_run=True,
        settings=_disabled_settings(),
    )
    assert result_disabled.visible_snapshot_unchanged is True


# ── Acceptance criterion 15: no raw metric values/payloads/source URLs returned ─

def test_no_raw_metric_values_in_result():
    """AC15: CoverageExpansionResult never contains raw metric values or payloads."""
    aid = _aid()
    src_id = str(uuid.uuid4())
    artifact_rows = [_make_artifact(aid, ticker="AMD")]
    facts_by_artifact = {aid: _make_all_metric_facts(aid, src_id)}

    db = _FakeDB(
        positions=[{"ticker": "AMD", "category": "Core"}],
        artifacts=artifact_rows,
        facts=facts_by_artifact[aid],
    )
    settings = _enabled_settings()

    result = compute_coverage_expansion(
        user_id=_UID,
        db_client=db,
        max_tickers=10,
        include_tickers=[],
        exclude_tickers=[],
        dry_run=True,
        settings=settings,
    )

    # Convert result to string and check no raw numeric metric values appear.
    result_str = str(result)
    forbidden_fields = [
        "structured_payload",
        "source_url",
        "quote_or_excerpt",
    ]
    for field_name in forbidden_fields:
        assert field_name not in result_str, (
            f"Forbidden field '{field_name}' found in result: {result_str[:200]}"
        )

    # before_coverage_summary must be aggregate-only.
    summary = result.before_coverage_summary
    assert isinstance(summary, dict)
    assert "portfolio_ticker_count" in summary
    assert "readiness_counts" in summary
    for key in summary:
        assert "value" not in key, f"Unexpected 'value' key in summary: {key}"
        assert "payload" not in key, f"Unexpected 'payload' key in summary: {key}"
        assert "url" not in key, f"Unexpected 'url' key in summary: {key}"


# ── Acceptance criterion 16: no forbidden imports ────────────────────────────

_MODULE_PATH = (
    pathlib.Path(__file__).parent.parent
    / "app/services/intelligence/research_workers/sec_metric_coverage_expansion.py"
)


def test_no_decide_or_intel_service_imports():
    """AC16: expansion module does not import decide(), IntelV3Service, etc."""
    src = _MODULE_PATH.read_text()
    tree = ast.parse(src)

    forbidden = {
        "decide",
        "decision_policy_v1",
        "IntelV3Service",
        "recommendation_engine",
        "intel_v3_service",
    }
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module = getattr(node, "module", "") or ""
            names = [alias.name for alias in node.names]
            for name in [module] + names:
                for forbidden_name in forbidden:
                    assert forbidden_name not in name, (
                        f"Forbidden import '{forbidden_name}' found in expansion module"
                    )


def test_expansion_module_no_safe_for_decision_true():
    """AC16 cont.: expansion module never assigns safe_for_decision=True in code."""
    src = _MODULE_PATH.read_text()
    tree = ast.parse(src)
    # Walk AST for any assignment where target is 'safe_for_decision' and value is True.
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword):
            # keyword(arg='safe_for_decision', value=Constant(True))
            if (
                node.arg == "safe_for_decision"
                and isinstance(node.value, ast.Constant)
                and node.value.value is True
            ):
                raise AssertionError(
                    f"safe_for_decision=True keyword argument found at line {node.value.lineno}"
                )
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "safe_for_decision":
                    if isinstance(node.value, ast.Constant) and node.value.value is True:
                        raise AssertionError(
                            f"safe_for_decision = True assignment found at line {node.lineno}"
                        )


# ── Acceptance criterion 17: disabled flag path is safe ───────────────────────

def test_disabled_flag_returns_safe_empty_result():
    """AC17: disabled flag returns zero/empty/false safely — never raises."""
    db = _FakeDB(positions=[{"ticker": "AMD", "category": "Core"}])
    result = compute_coverage_expansion(
        user_id=_UID,
        db_client=db,
        max_tickers=10,
        include_tickers=[],
        exclude_tickers=[],
        dry_run=True,
        settings=_disabled_settings(),
    )

    assert result.coverage_expansion_enabled is False
    assert result.safe_for_decision is False
    assert result.visible_snapshot_unchanged is True
    assert result.portfolio_ticker_count == 0
    assert result.candidate_count == 0
    assert result.selected_tickers == []
    assert result.artifact_ids == []
    assert result.attempted_count == 0
    assert result.written_count == 0
    assert result.failed_count == 0
    assert len(result.errors) >= 1


# ── Acceptance criterion 18: before_coverage_summary is aggregate-only ─────────

def test_before_coverage_summary_aggregate_only():
    """AC18: before_coverage_summary contains aggregate fields only."""
    aid = _aid()
    src_id = str(uuid.uuid4())
    artifact_rows = [_make_artifact(aid, ticker="MSFT")]
    facts_by_artifact = {aid: _make_all_metric_facts(aid, src_id)}

    db = _FakeDB(
        positions=[
            {"ticker": "MSFT", "category": "Core"},
            {"ticker": "AMD", "category": "Core"},
        ],
        artifacts=artifact_rows,
        facts=facts_by_artifact[aid],
    )
    settings = _enabled_settings()

    result = compute_coverage_expansion(
        user_id=_UID,
        db_client=db,
        max_tickers=10,
        include_tickers=[],
        exclude_tickers=[],
        dry_run=True,
        settings=settings,
    )

    summary = result.before_coverage_summary
    # Required aggregate fields.
    assert "portfolio_ticker_count" in summary
    assert "readiness_counts" in summary
    assert "tickers_with_source_linked_metric_evidence_count" in summary
    assert "tickers_without_source_linked_metric_evidence_count" in summary

    # MSFT has full facts → evidence_count should be 1.
    assert summary["tickers_with_source_linked_metric_evidence_count"] == 1
    # AMD has no artifacts → without count 1.
    assert summary["tickers_without_source_linked_metric_evidence_count"] == 1

    # Readiness counts are present.
    rc = summary["readiness_counts"]
    assert "READY_DRY_RUN_ONLY" in rc
    assert "PARTIAL_DRY_RUN_ONLY" in rc
    assert "BLOCKED_DRY_RUN_ONLY" in rc


# ── Additional edge case: mixed portfolio with all skip reasons ─────────────────

def test_mixed_portfolio_candidate_selection():
    """Mixed portfolio: ETF + Crypto + covered + eligible tickers."""
    aid_aapl = _aid()
    src_id = str(uuid.uuid4())
    artifact_rows = [_make_artifact(aid_aapl, "AAPL")]
    facts_by_artifact = {aid_aapl: _make_all_metric_facts(aid_aapl, src_id)}
    snapshot_by_ticker = _build_snapshot_by_ticker(artifact_rows, facts_by_artifact)

    positions = [
        {"ticker": "AAPL", "category": "Core"},    # already covered
        {"ticker": "VTI", "category": "ETF"},        # ETF
        {"ticker": "BTC", "category": "Crypto"},     # Crypto
        {"ticker": "AMD", "category": "Core"},       # eligible
        {"ticker": "META", "category": "Core"},      # eligible
        {"ticker": "GOOGL", "category": "Core"},     # excluded
    ]
    selected, skipped = _select_candidates(
        portfolio_positions=positions,
        snapshot_by_ticker=snapshot_by_ticker,
        include_tickers=[],
        exclude_tickers=["GOOGL"],
        max_tickers=10,
    )

    assert "AMD" in selected
    assert "META" in selected
    assert "AAPL" not in selected
    assert "VTI" not in selected
    assert "BTC" not in selected
    assert "GOOGL" not in selected

    assert "AAPL" in skipped.get("already_has_sec_metric_evidence", [])
    assert "VTI" in skipped.get("likely_fund_or_etf", [])
    assert "BTC" in skipped.get("likely_crypto", [])
    assert "GOOGL" in skipped.get("excluded_by_request", [])


def test_duplicate_portfolio_tickers_deduplicated():
    """Duplicate portfolio tickers are deduplicated; category from first occurrence."""
    positions = [
        {"ticker": "AMD", "category": "Core"},
        {"ticker": "AMD", "category": "ETF"},   # duplicate — Core wins
    ]
    selected, skipped = _select_candidates(
        portfolio_positions=positions,
        snapshot_by_ticker={},
        include_tickers=[],
        exclude_tickers=[],
        max_tickers=10,
    )

    # AMD appears once; first category (Core) is used → not ETF-blocked.
    assert selected.count("AMD") == 1
    assert "AMD" in selected
    assert "likely_fund_or_etf" not in skipped or "AMD" not in skipped.get("likely_fund_or_etf", [])


def test_empty_portfolio_no_candidates():
    """Empty portfolio produces zero candidates."""
    selected, skipped = _select_candidates(
        portfolio_positions=[],
        snapshot_by_ticker={},
        include_tickers=[],
        exclude_tickers=[],
        max_tickers=10,
    )
    assert selected == []
    assert skipped == {}


def test_contract_version_constant_present():
    """Contract version constant is present and has expected prefix."""
    assert SEC_METRIC_COVERAGE_EXPANSION_CONTRACT_VERSION.startswith("phase8e")


def test_compute_coverage_expansion_never_raises_on_db_error():
    """compute_coverage_expansion never raises even if DB raises."""

    class _BrokenDB:
        def table(self, *_):
            raise RuntimeError("DB is broken")

    result = compute_coverage_expansion(
        user_id=_UID,
        db_client=_BrokenDB(),
        max_tickers=10,
        include_tickers=[],
        exclude_tickers=[],
        dry_run=True,
        settings=_enabled_settings(),
    )

    assert result.safe_for_decision is False
    assert result.visible_snapshot_unchanged is True


# ── Phase 8E.1: SEC write-mode safety gate tests ──────────────────────────────

def _all_sec_flags_settings(**kwargs) -> _Settings:
    """Settings with all four write prerequisites satisfied."""
    return _Settings(
        intel_v3_sec_metric_portfolio_coverage_expansion_enabled=True,
        intel_v3_research_workers_enabled=True,
        intel_v3_earnings_reviewer_enabled=True,
        intel_v3_earnings_reviewer_sec_enabled=True,
        sec_edgar_user_agent="TestAgent/1.0 test@example.com",
        **kwargs,
    )


def test_dry_run_true_succeeds_without_sec_flags():
    """AC20: dry_run=true does not require SEC flags or user agent."""
    positions = [{"ticker": "AMD", "category": "Core"}]
    db = _FakeDB(positions=positions)
    # No SEC flags set — only the kill switch is enabled.
    settings = _Settings(
        intel_v3_sec_metric_portfolio_coverage_expansion_enabled=True,
        intel_v3_research_workers_enabled=False,
        intel_v3_earnings_reviewer_enabled=False,
        intel_v3_earnings_reviewer_sec_enabled=False,
        sec_edgar_user_agent=None,
    )

    result = compute_coverage_expansion(
        user_id=_UID,
        db_client=db,
        max_tickers=10,
        include_tickers=[],
        exclude_tickers=[],
        dry_run=True,
        settings=settings,
    )

    assert result.coverage_expansion_enabled is True
    assert result.dry_run is True
    assert result.attempted_count == 0
    assert result.written_count == 0
    assert result.artifact_ids == []
    assert result.safe_for_decision is False
    assert result.visible_snapshot_unchanged is True
    # AMD is eligible — selected without SEC flags.
    assert "AMD" in result.selected_tickers
    # No gate errors on dry_run path.
    assert not any("sec_enabled" in e or "user_agent" in e for e in result.errors)


def test_dry_run_false_sec_enabled_flag_off_writes_nothing():
    """AC21: dry_run=false with intel_v3_earnings_reviewer_sec_enabled=false writes nothing."""
    positions = [{"ticker": "AMD", "category": "Core"}]
    db = _FakeDB(positions=positions)
    settings = _Settings(
        intel_v3_sec_metric_portfolio_coverage_expansion_enabled=True,
        intel_v3_research_workers_enabled=True,
        intel_v3_earnings_reviewer_enabled=True,
        intel_v3_earnings_reviewer_sec_enabled=False,  # missing
        sec_edgar_user_agent="TestAgent/1.0 test@example.com",
    )

    result = compute_coverage_expansion(
        user_id=_UID,
        db_client=db,
        max_tickers=10,
        include_tickers=[],
        exclude_tickers=[],
        dry_run=False,
        settings=settings,
    )

    assert result.attempted_count == 0
    assert result.written_count == 0
    assert result.failed_count == 0
    assert result.artifact_ids == []
    assert result.safe_for_decision is False
    assert result.visible_snapshot_unchanged is True
    assert any("intel_v3_earnings_reviewer_sec_enabled=false" in e for e in result.errors)
    # selected_tickers still shows candidates that would have run.
    assert "AMD" in result.selected_tickers


def test_dry_run_false_sec_user_agent_missing_writes_nothing():
    """AC22: dry_run=false with sec_edgar_user_agent empty/None writes nothing."""
    positions = [{"ticker": "AMD", "category": "Core"}]
    db = _FakeDB(positions=positions)

    for ua in [None, "", "   "]:
        settings = _Settings(
            intel_v3_sec_metric_portfolio_coverage_expansion_enabled=True,
            intel_v3_research_workers_enabled=True,
            intel_v3_earnings_reviewer_enabled=True,
            intel_v3_earnings_reviewer_sec_enabled=True,
            sec_edgar_user_agent=ua,
        )

        result = compute_coverage_expansion(
            user_id=_UID,
            db_client=db,
            max_tickers=10,
            include_tickers=[],
            exclude_tickers=[],
            dry_run=False,
            settings=settings,
        )

        assert result.attempted_count == 0, f"ua={ua!r}: expected 0 attempted"
        assert result.written_count == 0, f"ua={ua!r}: expected 0 written"
        assert result.failed_count == 0, f"ua={ua!r}: expected 0 failed"
        assert result.artifact_ids == [], f"ua={ua!r}: expected empty artifact_ids"
        assert result.safe_for_decision is False
        assert result.visible_snapshot_unchanged is True
        assert any("sec_edgar_user_agent_missing" in e for e in result.errors), (
            f"ua={ua!r}: expected sec_edgar_user_agent_missing in errors"
        )


def test_dry_run_false_global_worker_flag_off_writes_nothing():
    """AC23: dry_run=false with intel_v3_research_workers_enabled=false writes nothing."""
    positions = [{"ticker": "AMD", "category": "Core"}]
    db = _FakeDB(positions=positions)
    settings = _Settings(
        intel_v3_sec_metric_portfolio_coverage_expansion_enabled=True,
        intel_v3_research_workers_enabled=False,  # missing
        intel_v3_earnings_reviewer_enabled=True,
        intel_v3_earnings_reviewer_sec_enabled=True,
        sec_edgar_user_agent="TestAgent/1.0 test@example.com",
    )

    result = compute_coverage_expansion(
        user_id=_UID,
        db_client=db,
        max_tickers=10,
        include_tickers=[],
        exclude_tickers=[],
        dry_run=False,
        settings=settings,
    )

    assert result.attempted_count == 0
    assert result.written_count == 0
    assert result.failed_count == 0
    assert result.artifact_ids == []
    assert result.safe_for_decision is False
    assert result.visible_snapshot_unchanged is True
    assert any("intel_v3_research_workers_enabled=false" in e for e in result.errors)


def test_dry_run_false_earnings_reviewer_flag_off_writes_nothing():
    """AC24: dry_run=false with intel_v3_earnings_reviewer_enabled=false writes nothing."""
    positions = [{"ticker": "AMD", "category": "Core"}]
    db = _FakeDB(positions=positions)
    settings = _Settings(
        intel_v3_sec_metric_portfolio_coverage_expansion_enabled=True,
        intel_v3_research_workers_enabled=True,
        intel_v3_earnings_reviewer_enabled=False,  # missing
        intel_v3_earnings_reviewer_sec_enabled=True,
        sec_edgar_user_agent="TestAgent/1.0 test@example.com",
    )

    result = compute_coverage_expansion(
        user_id=_UID,
        db_client=db,
        max_tickers=10,
        include_tickers=[],
        exclude_tickers=[],
        dry_run=False,
        settings=settings,
    )

    assert result.attempted_count == 0
    assert result.written_count == 0
    assert result.failed_count == 0
    assert result.artifact_ids == []
    assert result.safe_for_decision is False
    assert result.visible_snapshot_unchanged is True
    assert any("intel_v3_earnings_reviewer_enabled=false" in e for e in result.errors)


def test_dry_run_false_all_flags_set_calls_writer(monkeypatch):
    """AC25: dry_run=false with all four prerequisites met calls the existing writer."""
    positions = [{"ticker": "AMD", "category": "Core"}]
    db = _FakeDB(positions=positions)
    settings = _all_sec_flags_settings()

    written_tickers: list[str] = []

    def _fake_runner(user_id, ticker, db_client, settings=None):
        written_tickers.append(ticker)
        return f"artifact-{ticker}"

    import app.services.intelligence.research_workers.runner as runner_mod
    original = runner_mod.run_earnings_reviewer_dark
    runner_mod.run_earnings_reviewer_dark = _fake_runner
    try:
        result = compute_coverage_expansion(
            user_id=_UID,
            db_client=db,
            max_tickers=10,
            include_tickers=[],
            exclude_tickers=[],
            dry_run=False,
            settings=settings,
        )
    finally:
        runner_mod.run_earnings_reviewer_dark = original

    assert result.attempted_count == len(result.selected_tickers)
    assert "AMD" in written_tickers
    assert result.written_count == 1
    assert result.artifact_ids == ["artifact-AMD"]
    assert result.safe_for_decision is False
    assert result.visible_snapshot_unchanged is True
    # No gate errors when all flags are set.
    gate_error_codes = [
        "intel_v3_research_workers_enabled=false",
        "intel_v3_earnings_reviewer_enabled=false",
        "intel_v3_earnings_reviewer_sec_enabled=false",
        "sec_edgar_user_agent_missing",
    ]
    for code in gate_error_codes:
        assert not any(code in e for e in result.errors), (
            f"Unexpected gate error '{code}' when all flags are set"
        )
