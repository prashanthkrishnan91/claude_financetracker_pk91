"""Stage 5J focused tests — Research Evidence Coverage Read Model v1.

Acceptance criteria verified here:
  1. Usable SEC CompanyFacts artifact counted for ticker (READY).
  2. Usable FRED macro artifact counted at portfolio level (READY).
  3. Missing lanes reported honestly (MISSING, no fabricated evidence).
  4. Suppressed artifacts excluded from ready evidence (SUPPRESSED, not READY).
  5. Duplicate/idempotent artifacts choose latest active deterministically.
  6. No DB writes triggered (read-only).
  7. No secret/API key fields ever returned in summary output.
  8. safe_for_decision is always False on the returned summary.
  9. USABLE_WITH_LIMITATIONS reported as LIMITED, still counted as ready.
 10. STALE/UNKNOWN freshness overrides USABLE → STATUS_STALE_OR_UNKNOWN.

No production Supabase dependency — all fakes defined locally.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pytest

from app.services.intelligence.v3.research_evidence_coverage_read_model_v1 import (
    LANE_FUNDAMENTALS,
    LANE_MACRO_CONTEXT,
    LANE_NEWS_SENTIMENT,
    LANE_SEC_COMPANY_FACTS,
    LANE_TECHNICALS,
    READ_MODEL_VERSION,
    STATUS_LIMITED,
    STATUS_MISSING,
    STATUS_NOT_EVALUABLE,
    STATUS_READY,
    STATUS_STALE_OR_UNKNOWN,
    STATUS_SUPPRESSED,
    compute_research_evidence_coverage,
    log_coverage_summary,
)


_USER_ID = "user-stage5j-test"


# ── Fakes ─────────────────────────────────────────────────────────────────────


@dataclass
class _FakeDB:
    rows: list[dict[str, Any]] = field(default_factory=list)
    select_call_count: int = 0
    write_attempts: int = 0


class _FakeQuery:
    def __init__(self, db: _FakeDB, table_name: str) -> None:
        self._db = db
        self._table = table_name
        self._filters: dict[str, Any] = {}
        self._select_cols: Optional[str] = None
        self._op: Optional[str] = None

    def select(self, cols: str = "*") -> "_FakeQuery":
        self._op = "select"
        self._select_cols = cols
        return self

    def insert(self, *args, **kwargs):
        self._db.write_attempts += 1
        raise AssertionError("Read model must never insert")

    def update(self, *args, **kwargs):
        self._db.write_attempts += 1
        raise AssertionError("Read model must never update")

    def delete(self, *args, **kwargs):
        self._db.write_attempts += 1
        raise AssertionError("Read model must never delete")

    def eq(self, col: str, val: Any) -> "_FakeQuery":
        self._filters[col] = val
        return self

    def execute(self) -> Any:
        self._db.select_call_count += 1
        if self._table != "research_artifacts" or self._op != "select":
            class _Empty:
                data = []
            return _Empty()
        matched = []
        for row in self._db.rows:
            if all(row.get(k) == v for k, v in self._filters.items()):
                matched.append(row)

        class _Res:
            data = matched
        return _Res()


class _FakeClient:
    def __init__(self, db: _FakeDB) -> None:
        self._db = db

    def table(self, name: str) -> _FakeQuery:
        return _FakeQuery(self._db, name)


# ── Row builders ──────────────────────────────────────────────────────────────


def _now_iso(offset_seconds: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).isoformat()


def _make_artifact_row(
    *,
    artifact_type: str,
    skill_pack: str,
    scope_kind: str,
    ticker: Optional[str],
    usability_label: str = "USABLE",
    is_usable: Optional[bool] = None,
    suppression_reason: Optional[str] = None,
    completeness_band: str = "COMPLETE",
    strongest_authority_level: str = "PRIMARY_AUTHORITY",
    is_evaluable: bool = True,
    has_contradictions: bool = False,
    contradiction_count: int = 0,
    freshness_status: str = "FRESH",
    confidence_or_trust_level: str = "MEDIUM",
    generated_at: Optional[str] = None,
    is_active: bool = True,
    model_version: Optional[str] = "model.v1",
    artifact_id: Optional[str] = None,
    secret_field: Optional[str] = None,
) -> dict[str, Any]:
    if is_usable is None:
        is_usable = usability_label in {"USABLE", "USABLE_WITH_LIMITATIONS"}
    payload = {
        "truth_usability_assessment": {
            "usability_label": usability_label,
            "is_usable": is_usable,
            "suppression_reason": suppression_reason,
            "limitations": ["test"],
            "no_guessing": True,
        },
        "source_credibility_assessment": {
            "strongest_authority_level": strongest_authority_level,
            "source_count": 1,
            "is_insufficient": False,
        },
        "contradiction_assessment": {
            "is_evaluable": is_evaluable,
            "has_contradictions": has_contradictions,
            "contradiction_count": contradiction_count,
        },
        "evidence_completeness_assessment": {
            "completeness_band": completeness_band,
            "is_evaluable": True,
        },
    }
    # Defensive: simulate a secret leaking into payload (should never escape).
    if secret_field:
        payload["api_key"] = secret_field
    return {
        "id": artifact_id or str(uuid.uuid4()),
        "user_id": _USER_ID,
        "artifact_type": artifact_type,
        "skill_pack": skill_pack,
        "scope_kind": scope_kind,
        "ticker": ticker,
        "is_active": is_active,
        "safe_for_decision": False,
        "freshness_status": freshness_status,
        "confidence_or_trust_level": confidence_or_trust_level,
        "generated_at": generated_at or _now_iso(),
        "expires_at": None,
        "model_version": model_version,
        "payload": payload,
    }


def _sec_row(ticker: str, **kw) -> dict[str, Any]:
    return _make_artifact_row(
        artifact_type="fundamental_quality",
        skill_pack="sec_companyfacts_evidence_v1",
        scope_kind="ticker",
        ticker=ticker,
        **kw,
    )


def _macro_row(**kw) -> dict[str, Any]:
    return _make_artifact_row(
        artifact_type="portfolio_exposure",
        skill_pack="fred_macro_evidence_v1",
        scope_kind="portfolio",
        ticker=None,
        **kw,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestUsableSecArtifactCountedPerTicker:
    def test_usable_sec_artifact_marks_lane_ready(self) -> None:
        db = _FakeDB(rows=[_sec_row("AAPL")])
        summary = compute_research_evidence_coverage(
            user_id=_USER_ID, tickers=["AAPL"], db_client=_FakeClient(db),
        )
        cov = summary.ticker_coverage["AAPL"].lanes[LANE_SEC_COMPANY_FACTS]
        assert cov.status == STATUS_READY
        assert cov.is_usable is True
        assert cov.artifact_id is not None
        assert cov.usability_label == "USABLE"
        assert cov.source_authority == "PRIMARY_AUTHORITY"
        assert cov.completeness_band == "COMPLETE"
        assert cov.has_contradictions is False
        assert summary.ready_artifact_count >= 1
        assert summary.lane_counts.get(LANE_SEC_COMPANY_FACTS) == 1
        assert summary.usability_counts.get("USABLE") == 1


class TestUsableFredMacroCountedAtPortfolioLevel:
    def test_macro_row_populates_portfolio_macro_coverage(self) -> None:
        db = _FakeDB(rows=[_macro_row()])
        summary = compute_research_evidence_coverage(
            user_id=_USER_ID, tickers=["AAPL", "MSFT"], db_client=_FakeClient(db),
        )
        macro = summary.portfolio_macro_coverage
        assert macro.scope_kind == "portfolio"
        assert macro.ticker is None
        assert macro.lane == LANE_MACRO_CONTEXT
        assert macro.status == STATUS_READY
        assert macro.is_usable is True
        # Macro must NOT be merged into ticker coverage.
        for t in ("AAPL", "MSFT"):
            assert LANE_MACRO_CONTEXT not in summary.ticker_coverage[t].lanes
        assert summary.lane_counts.get(LANE_MACRO_CONTEXT) == 1


class TestMissingLanesReportedHonestly:
    def test_missing_lanes_appear_as_missing_not_fabricated(self) -> None:
        # No artifacts at all.
        db = _FakeDB(rows=[])
        summary = compute_research_evidence_coverage(
            user_id=_USER_ID, tickers=["AAPL"], db_client=_FakeClient(db),
        )
        for lane in (
            LANE_SEC_COMPANY_FACTS,
            LANE_FUNDAMENTALS,
            LANE_TECHNICALS,
            LANE_NEWS_SENTIMENT,
        ):
            cov = summary.ticker_coverage["AAPL"].lanes[lane]
            assert cov.status == STATUS_MISSING
            assert cov.artifact_id is None
            assert cov.usability_label is None
            assert cov.is_usable is False
        assert summary.portfolio_macro_coverage.status == STATUS_MISSING
        assert summary.ready_artifact_count == 0
        assert summary.missing_lane_counts.get(LANE_SEC_COMPANY_FACTS) == 1
        assert summary.missing_lane_counts.get(LANE_MACRO_CONTEXT) == 1


class TestSuppressedExcludedFromReady:
    def test_suppressed_contradicted_not_counted_as_ready(self) -> None:
        db = _FakeDB(rows=[_sec_row(
            "AAPL",
            usability_label="SUPPRESSED_CONTRADICTED",
            is_usable=False,
            suppression_reason="material_contradiction_detected",
            has_contradictions=True,
            contradiction_count=2,
        )])
        summary = compute_research_evidence_coverage(
            user_id=_USER_ID, tickers=["AAPL"], db_client=_FakeClient(db),
        )
        cov = summary.ticker_coverage["AAPL"].lanes[LANE_SEC_COMPANY_FACTS]
        assert cov.status == STATUS_SUPPRESSED
        assert cov.is_usable is False
        assert cov.suppression_reason == "material_contradiction_detected"
        assert cov.has_contradictions is True
        assert summary.ready_artifact_count == 0
        assert summary.suppressed_counts.get("SUPPRESSED_CONTRADICTED") == 1


class TestDuplicateActiveArtifactsPickLatest:
    def test_latest_generated_at_wins_when_multiple_active_rows_exist(self) -> None:
        old_id, new_id = "old-uuid-aaaa", "new-uuid-bbbb"
        old_row = _sec_row(
            "AAPL",
            artifact_id=old_id,
            generated_at=_now_iso(offset_seconds=-3600),
            usability_label="SUPPRESSED_INCOMPLETE",
            is_usable=False,
            suppression_reason="evidence_completeness_thin",
            completeness_band="THIN",
        )
        new_row = _sec_row(
            "AAPL",
            artifact_id=new_id,
            generated_at=_now_iso(offset_seconds=0),
            usability_label="USABLE",
        )
        db = _FakeDB(rows=[old_row, new_row])
        summary = compute_research_evidence_coverage(
            user_id=_USER_ID, tickers=["AAPL"], db_client=_FakeClient(db),
        )
        cov = summary.ticker_coverage["AAPL"].lanes[LANE_SEC_COMPANY_FACTS]
        assert cov.artifact_id == new_id
        assert cov.status == STATUS_READY


class TestReadModelIsReadOnly:
    def test_no_writes_attempted(self) -> None:
        db = _FakeDB(rows=[_sec_row("AAPL"), _macro_row()])
        compute_research_evidence_coverage(
            user_id=_USER_ID, tickers=["AAPL"], db_client=_FakeClient(db),
        )
        assert db.write_attempts == 0
        # Exactly one SELECT against research_artifacts.
        assert db.select_call_count == 1


class TestNoSecretLeakage:
    def test_summary_dict_never_contains_payload_or_secrets(self) -> None:
        db = _FakeDB(rows=[_sec_row("AAPL", secret_field="FRED_API_KEY_LEAK")])
        summary = compute_research_evidence_coverage(
            user_id=_USER_ID, tickers=["AAPL"], db_client=_FakeClient(db),
        )
        # Serialize the entire summary and assert nothing leaked through.
        blob = json.dumps(summary.to_dict())
        assert "FRED_API_KEY_LEAK" not in blob
        assert "api_key" not in blob
        # No raw payload key escapes either.
        assert "\"payload\":" not in blob


class TestSafeForDecisionAlwaysFalse:
    def test_summary_safe_for_decision_false(self) -> None:
        db = _FakeDB(rows=[_sec_row("AAPL"), _macro_row()])
        summary = compute_research_evidence_coverage(
            user_id=_USER_ID, tickers=["AAPL"], db_client=_FakeClient(db),
        )
        assert summary.safe_for_decision is False
        assert summary.no_guessing is True
        assert summary.schema_version == READ_MODEL_VERSION


class TestLimitedAndStaleSemantics:
    def test_usable_with_limitations_maps_to_limited_and_is_ready(self) -> None:
        db = _FakeDB(rows=[_sec_row(
            "AAPL",
            usability_label="USABLE_WITH_LIMITATIONS",
            is_usable=True,
            completeness_band="PARTIAL",
        )])
        summary = compute_research_evidence_coverage(
            user_id=_USER_ID, tickers=["AAPL"], db_client=_FakeClient(db),
        )
        cov = summary.ticker_coverage["AAPL"].lanes[LANE_SEC_COMPANY_FACTS]
        assert cov.status == STATUS_LIMITED
        assert cov.is_usable is True
        assert summary.ready_artifact_count == 1

    def test_stale_freshness_overrides_usable_to_stale_or_unknown(self) -> None:
        db = _FakeDB(rows=[_sec_row(
            "AAPL",
            usability_label="USABLE",
            freshness_status="STALE",
        )])
        summary = compute_research_evidence_coverage(
            user_id=_USER_ID, tickers=["AAPL"], db_client=_FakeClient(db),
        )
        cov = summary.ticker_coverage["AAPL"].lanes[LANE_SEC_COMPANY_FACTS]
        assert cov.status == STATUS_STALE_OR_UNKNOWN
        assert cov.is_usable is False
        assert summary.stale_or_unknown_counts.get(LANE_SEC_COMPANY_FACTS) == 1


class TestNotEvaluableArtifact:
    def test_not_evaluable_label_maps_to_not_evaluable_status(self) -> None:
        db = _FakeDB(rows=[_sec_row(
            "AAPL",
            usability_label="NOT_EVALUABLE",
            is_usable=False,
            completeness_band="NOT_EVALUABLE",
            is_evaluable=False,
        )])
        summary = compute_research_evidence_coverage(
            user_id=_USER_ID, tickers=["AAPL"], db_client=_FakeClient(db),
        )
        cov = summary.ticker_coverage["AAPL"].lanes[LANE_SEC_COMPANY_FACTS]
        assert cov.status == STATUS_NOT_EVALUABLE
        assert cov.is_usable is False
        assert summary.ready_artifact_count == 0


class TestNormalizationAndDedup:
    def test_lowercase_and_duplicate_tickers_collapse_and_uppercase(self) -> None:
        db = _FakeDB(rows=[_sec_row("AAPL")])
        summary = compute_research_evidence_coverage(
            user_id=_USER_ID,
            tickers=["aapl", "AAPL", "  AAPL  "],
            db_client=_FakeClient(db),
        )
        assert summary.portfolio_ticker_count == 1
        assert "AAPL" in summary.ticker_coverage


class TestDbErrorFailsSoft:
    class _BrokenClient:
        def table(self, name: str) -> Any:
            raise RuntimeError("simulated DB outage")

    def test_query_failure_returns_missing_summary_with_error(self) -> None:
        summary = compute_research_evidence_coverage(
            user_id=_USER_ID,
            tickers=["AAPL"],
            db_client=self._BrokenClient(),
        )
        assert summary.ready_artifact_count == 0
        assert summary.errors and any("query_failure" in e for e in summary.errors)
        cov = summary.ticker_coverage["AAPL"].lanes[LANE_SEC_COMPANY_FACTS]
        assert cov.status == STATUS_MISSING


class TestLogCoverageSummaryEmitsCompactLine:
    def test_log_does_not_dump_payload(self, caplog) -> None:
        db = _FakeDB(rows=[_sec_row("AAPL", secret_field="LEAK_TOKEN"), _macro_row()])
        summary = compute_research_evidence_coverage(
            user_id=_USER_ID, tickers=["AAPL"], db_client=_FakeClient(db),
        )
        caplog.clear()
        with caplog.at_level("INFO"):
            log_coverage_summary(summary)
        messages = " ".join(rec.getMessage() for rec in caplog.records)
        assert "research_evidence_coverage_summary" in messages
        assert "LEAK_TOKEN" not in messages
        assert "api_key" not in messages


# ── Endpoint gate tests ────────────────────────────────────────────────────────
# These call the endpoint function directly (not via HTTP) so they work without
# a running server or Supabase. The gate logic is sync/fast.

class TestEndpointCertGateBlocks:
    """Endpoint must be unreachable when cert flag is off or secret is wrong."""

    def _make_settings(self, *, cert_enabled=True, secret="testsecret", coverage_enabled=True, user_id=None):
        from types import SimpleNamespace
        return SimpleNamespace(
            finance_runtime_cert_enabled=cert_enabled,
            finance_runtime_cert_secret=secret,
            finance_runtime_cert_user_id=user_id or str(uuid.uuid4()),
            finance_runtime_cert_user_email="cert@local",
            intel_v3_evidence_coverage_diagnostics_enabled=coverage_enabled,
        )

    def test_cert_flag_disabled_raises_404(self):
        import hmac as _hmac
        from fastapi import HTTPException
        from app.routers.diagnostics import _ensure_cert_enabled
        from unittest.mock import patch

        settings = self._make_settings(cert_enabled=False)
        with patch("app.routers.diagnostics.get_settings", return_value=settings):
            with pytest.raises(HTTPException) as exc_info:
                _ensure_cert_enabled("testsecret")
            assert exc_info.value.status_code == 404

    def test_wrong_secret_raises_403(self):
        from fastapi import HTTPException
        from app.routers.diagnostics import _ensure_cert_enabled
        from unittest.mock import patch

        settings = self._make_settings(cert_enabled=True, secret="correct")
        with patch("app.routers.diagnostics.get_settings", return_value=settings):
            with pytest.raises(HTTPException) as exc_info:
                _ensure_cert_enabled("WRONG")
            assert exc_info.value.status_code == 403

    def test_missing_secret_header_raises_403(self):
        from fastapi import HTTPException
        from app.routers.diagnostics import _ensure_cert_enabled
        from unittest.mock import patch

        settings = self._make_settings(cert_enabled=True, secret="correct")
        with patch("app.routers.diagnostics.get_settings", return_value=settings):
            with pytest.raises(HTTPException) as exc_info:
                _ensure_cert_enabled(None)
            assert exc_info.value.status_code == 403

    def test_coverage_flag_disabled_raises_403(self):
        """Even with valid cert, endpoint 403s when coverage diagnostics flag is off."""
        import asyncio
        from fastapi import HTTPException
        from app.routers.diagnostics import get_research_evidence_coverage, ResearchEvidenceCoverageRequest
        from types import SimpleNamespace
        from unittest.mock import patch, AsyncMock

        _uid = str(uuid.uuid4())
        settings = self._make_settings(coverage_enabled=False, user_id=_uid)
        fake_user = SimpleNamespace(id=_uid)
        payload = ResearchEvidenceCoverageRequest(tickers=["AAPL"])

        with patch("app.routers.diagnostics.get_settings", return_value=settings):
            with pytest.raises(HTTPException) as exc_info:
                asyncio.get_event_loop().run_until_complete(
                    get_research_evidence_coverage(payload=payload, user=fake_user)
                )
            assert exc_info.value.status_code == 403


class TestEndpointReadOnlyAndSafe:
    """Endpoint response must be read-only and free of raw payloads / secrets."""

    def _run_endpoint(self, tickers, artifact_rows):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import patch
        from app.routers.diagnostics import get_research_evidence_coverage, ResearchEvidenceCoverageRequest

        _uid = str(uuid.uuid4())
        settings = SimpleNamespace(
            finance_runtime_cert_enabled=True,
            finance_runtime_cert_secret="testsecret",
            finance_runtime_cert_user_id=_uid,
            finance_runtime_cert_user_email="cert@local",
            intel_v3_evidence_coverage_diagnostics_enabled=True,
        )
        fake_user = SimpleNamespace(id=_uid)
        db = _FakeDB(rows=artifact_rows)
        fake_client = _FakeClient(db)
        payload = ResearchEvidenceCoverageRequest(tickers=tickers)

        with patch("app.routers.diagnostics.get_settings", return_value=settings):
            with patch("app.routers.diagnostics.get_supabase_client", return_value=fake_client):
                result = asyncio.get_event_loop().run_until_complete(
                    get_research_evidence_coverage(payload=payload, user=fake_user)
                )
        return result, db

    def test_response_contains_no_raw_payload_or_secrets(self):
        rows = [_sec_row("AAPL", secret_field="SUPERSECRET_KEY")]
        result, db = self._run_endpoint(["AAPL"], rows)
        blob = json.dumps(result)
        assert "SUPERSECRET_KEY" not in blob
        assert "api_key" not in blob
        assert '"payload"' not in blob

    def test_no_db_writes_from_endpoint(self):
        rows = [_sec_row("AAPL"), _macro_row()]
        result, db = self._run_endpoint(["AAPL"], rows)
        assert db.write_attempts == 0

    def test_safe_for_decision_false_in_response(self):
        rows = [_sec_row("AAPL"), _macro_row()]
        result, _ = self._run_endpoint(["AAPL"], rows)
        assert result["safe_for_decision"] is False

    def test_etf_ticker_with_no_sec_artifact_reports_missing_not_fake(self):
        # ETFs (e.g. SPY) should never have fake SEC company-facts evidence.
        # The read model must show MISSING for sec_company_facts, not a fabricated READY.
        # (The runner already skips SEC for ETFs; the read model must honestly report what's in DB.)
        rows = []  # no SEC artifact for SPY — correct: runner skipped it
        result, _ = self._run_endpoint(["SPY"], rows)
        spy_cov = result["ticker_coverage"]["SPY"]["lanes"]
        assert spy_cov[LANE_SEC_COMPANY_FACTS]["status"] == STATUS_MISSING
        assert spy_cov[LANE_SEC_COMPANY_FACTS]["is_usable"] is False
        assert spy_cov[LANE_SEC_COMPANY_FACTS]["artifact_id"] is None

    def test_crypto_ticker_with_no_sec_artifact_reports_missing_not_fake(self):
        rows = []  # no SEC artifact for BTC — correct: runner skipped it
        result, _ = self._run_endpoint(["BTC"], rows)
        btc_cov = result["ticker_coverage"]["BTC"]["lanes"]
        assert btc_cov[LANE_SEC_COMPANY_FACTS]["status"] == STATUS_MISSING
        assert btc_cov[LANE_SEC_COMPANY_FACTS]["is_usable"] is False

    def test_ticker_cap_at_200(self):
        # Supply 205 tickers; endpoint must cap at 200.
        tickers = [f"T{i:04d}" for i in range(205)]
        rows = []
        result, _ = self._run_endpoint(tickers, rows)
        assert result["portfolio_ticker_count"] == 200


class TestDispatchLogFlag:
    """Coverage dispatch log must emit only when flag is on; never dumps payload."""

    def test_dispatch_log_no_payload_in_log_output(self, caplog):
        """log_coverage_summary never dumps payload even with secret in artifact."""
        db = _FakeDB(rows=[_sec_row("AAPL", secret_field="SECRET_DISPATCH_LOG")])
        summary = compute_research_evidence_coverage(
            user_id=_USER_ID, tickers=["AAPL"], db_client=_FakeClient(db),
        )
        with caplog.at_level("INFO"):
            log_coverage_summary(summary)
        messages = " ".join(rec.getMessage() for rec in caplog.records)
        assert "SECRET_DISPATCH_LOG" not in messages
        assert "source_url" not in messages
