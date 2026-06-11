"""Stage 9N — ETF Provider Decision Matrix v1 tests.

Fixture-based only — no live HTTP calls, no SQL, no artifact writes.

Tests prove:
  N1–N4:   SEC NPORT for VTI/SCHD/VXUS is manual_research_required (not canonical_ready).
  N5–N7:   SEC NPORT for SPY/QQQ is canonical_ready.
  N8–N10:  Alpha Vantage missing-date remains supplemental_only, not canonical.
  N11–N13: FMP 402 is rejected_paywalled.
  N14–N16: Issuer-official is manual_research_required until url/identity/date confirmed.
  N17:     GLD commodity trust is not_applicable.
  N18–N20: S-grade canonical gate invariants (mirrors Stage 9K).
  N21:     build_provider_decision_matrix returns complete, serializable output.
  N22:     No provider has canonical_ready=True except sec_nport_spy_qqq.
  N23:     safe_for_decision=False on every provider.
  N24:     Stage 9K gate not loosened (check_canonical_gate mirrors same criteria).
  N25:     Patch-loop stop reasons are present for all three problem providers.
  N26:     ETF class next paths cover all five classes.
  N27–N29: ETF class next paths recommend correct provider paths.
  N30:     classify_provider returns None for unknown provider_id.
  N31:     get_next_path_for_etf_class returns None for unknown class.
  N32:     Matrix version constant is present.
  N33:     S-grade criteria list has all 6 required dimensions.
  N34–N37: check_canonical_gate rejects each missing criterion independently and passes
           when all criteria are met.
  N38:     AV supplemental classifier still returns canonical_ready=False (regression).
  N39:     All provider classifications are one of the defined constants.
"""
from __future__ import annotations

import pytest


# ── Helpers — deferred imports to avoid app/__init__.py import chain ──────────

def _matrix_module():
    from app.services.intelligence.research_workers.etf_provider_decision_matrix_v1 import (
        CANONICAL_READY,
        SUPPLEMENTAL_ONLY,
        MANUAL_RESEARCH_REQUIRED,
        REJECTED_PAYWALLED,
        REJECTED_INSUFFICIENT,
        NOT_APPLICABLE,
        MATRIX_VERSION,
        S_GRADE_CRITERIA,
        build_provider_decision_matrix,
        check_canonical_gate,
        classify_provider,
        get_next_path_for_etf_class,
    )
    return (
        CANONICAL_READY, SUPPLEMENTAL_ONLY, MANUAL_RESEARCH_REQUIRED,
        REJECTED_PAYWALLED, REJECTED_INSUFFICIENT, NOT_APPLICABLE,
        MATRIX_VERSION, S_GRADE_CRITERIA,
        build_provider_decision_matrix, check_canonical_gate,
        classify_provider, get_next_path_for_etf_class,
    )


def _classify_provider(provider_id: str):
    from app.services.intelligence.research_workers.etf_provider_decision_matrix_v1 import classify_provider
    return classify_provider(provider_id)


def _build_matrix():
    from app.services.intelligence.research_workers.etf_provider_decision_matrix_v1 import build_provider_decision_matrix
    return build_provider_decision_matrix()


def _check_gate(**kwargs):
    from app.services.intelligence.research_workers.etf_provider_decision_matrix_v1 import check_canonical_gate
    return check_canonical_gate(**kwargs)


def _get_next_path(etf_class: str):
    from app.services.intelligence.research_workers.etf_provider_decision_matrix_v1 import get_next_path_for_etf_class
    return get_next_path_for_etf_class(etf_class)


def _classify_av(holdings_count, weights_available, as_of_date=None, **kwargs):
    from app.services.intelligence.research_workers.alpha_vantage_supplemental_classifier_v1 import classify_av_etf_output
    return classify_av_etf_output(holdings_count, weights_available, as_of_date, **kwargs)


# ── N1–N4: SEC NPORT VTI/SCHD/VXUS is manual_research_required ───────────────

class TestNportVtiSchdVxus:
    def test_n1_classification_is_manual_research_required(self):
        rec = _classify_provider("sec_nport_vti_schd_vxus")
        assert rec["classification"] == "manual_research_required"

    def test_n2_canonical_ready_false(self):
        rec = _classify_provider("sec_nport_vti_schd_vxus")
        assert rec["canonical_ready"] is False

    def test_n3_safe_for_decision_false(self):
        rec = _classify_provider("sec_nport_vti_schd_vxus")
        assert rec["safe_for_decision"] is False

    def test_n4_rejection_reasons_include_wrong_cik(self):
        rec = _classify_provider("sec_nport_vti_schd_vxus")
        assert any("cik" in r.lower() for r in rec["rejection_reasons"])

    def test_n4b_runtime_evidence_references_stage9m(self):
        rec = _classify_provider("sec_nport_vti_schd_vxus")
        assert "9M" in rec["runtime_evidence"]

    def test_n4c_runtime_evidence_mentions_vti_cik(self):
        rec = _classify_provider("sec_nport_vti_schd_vxus")
        assert "764180" in rec["runtime_evidence"]

    def test_n4d_runtime_evidence_mentions_schd_cik(self):
        rec = _classify_provider("sec_nport_vti_schd_vxus")
        assert "1477379" in rec["runtime_evidence"]

    def test_n4e_runtime_evidence_mentions_vxus_cik(self):
        rec = _classify_provider("sec_nport_vti_schd_vxus")
        assert "1004244" in rec["runtime_evidence"]


# ── N5–N7: SEC NPORT SPY/QQQ is canonical_ready ───────────────────────────────

class TestNportSpyQqq:
    def test_n5_classification_is_canonical_ready(self):
        rec = _classify_provider("sec_nport_spy_qqq")
        assert rec["classification"] == "canonical_ready"

    def test_n6_canonical_ready_true(self):
        rec = _classify_provider("sec_nport_spy_qqq")
        assert rec["canonical_ready"] is True

    def test_n7_no_rejection_reasons(self):
        rec = _classify_provider("sec_nport_spy_qqq")
        assert rec["rejection_reasons"] == []

    def test_n7b_identity_verified_true(self):
        rec = _classify_provider("sec_nport_spy_qqq")
        assert rec["criteria"]["identity_verified"] is True

    def test_n7c_source_authority_is_sec(self):
        rec = _classify_provider("sec_nport_spy_qqq")
        assert rec["criteria"]["source_authority"] == "sec_primary_authority"


# ── N8–N10: Alpha Vantage is supplemental_only ────────────────────────────────

class TestAlphaVantageMatrix:
    def test_n8_classification_is_supplemental_only(self):
        rec = _classify_provider("alpha_vantage_etf_profile")
        assert rec["classification"] == "supplemental_only"

    def test_n9_canonical_ready_false(self):
        rec = _classify_provider("alpha_vantage_etf_profile")
        assert rec["canonical_ready"] is False

    def test_n10_as_of_date_criterion_false(self):
        rec = _classify_provider("alpha_vantage_etf_profile")
        assert rec["criteria"]["as_of_or_report_date_present"] is False

    def test_n10b_rejection_includes_date_absent(self):
        rec = _classify_provider("alpha_vantage_etf_profile")
        assert any("date" in r or "as_of" in r for r in rec["rejection_reasons"])

    def test_n10c_rejection_includes_vxus_partial(self):
        rec = _classify_provider("alpha_vantage_etf_profile")
        assert any("partial" in r or "37" in r for r in rec["rejection_reasons"])

    def test_n10d_supplemental_only_not_promoted_by_holdings_presence(self):
        rec = _classify_provider("alpha_vantage_etf_profile")
        assert rec["criteria"]["weights_available"] is True
        assert rec["criteria"]["holdings_count_nonzero"] is True
        assert rec["classification"] == "supplemental_only"


# ── N11–N13: FMP is rejected_paywalled ────────────────────────────────────────

class TestFmpMatrix:
    def test_n11_classification_is_rejected_paywalled(self):
        rec = _classify_provider("fmp_etf_holdings")
        assert rec["classification"] == "rejected_paywalled"

    def test_n12_entitlement_status_mentions_402(self):
        rec = _classify_provider("fmp_etf_holdings")
        assert "402" in rec["criteria"]["entitlement_status"]

    def test_n13_rejection_includes_http_402(self):
        rec = _classify_provider("fmp_etf_holdings")
        assert any("402" in r for r in rec["rejection_reasons"])

    def test_n13b_canonical_ready_false(self):
        rec = _classify_provider("fmp_etf_holdings")
        assert rec["canonical_ready"] is False

    def test_n13c_safe_for_decision_false(self):
        rec = _classify_provider("fmp_etf_holdings")
        assert rec["safe_for_decision"] is False


# ── N14–N16: Issuer-official matrix records ───────────────────────────────────
# N14: issuer_official_vanguard is now rejected_insufficient (Stage 9O live run: 404).
# N15–N16: schwab and ssga remain manual_research_required (no live run attempted).

class TestIssuerOfficialMatrix:
    def test_n14_vanguard_is_rejected_insufficient_after_stage9o(self):
        """N14: Stage 9O live run returned 404 for VTI/VXUS — vanguard is rejected_insufficient."""
        rec = _classify_provider("issuer_official_vanguard")
        assert rec["classification"] == "rejected_insufficient"

    def test_n14b_vanguard_stage9o_runtime_evidence_present(self):
        """N14b: runtime_evidence references Stage 9O and 404 result."""
        rec = _classify_provider("issuer_official_vanguard")
        assert "9O" in rec["runtime_evidence"] or "stage9o" in rec["runtime_evidence"].lower()
        assert "404" in rec["runtime_evidence"]

    def test_n14c_vanguard_rejection_includes_404_reason(self):
        """N14c: rejection_reasons include the live 404 outcome."""
        rec = _classify_provider("issuer_official_vanguard")
        assert any("404" in r for r in rec["rejection_reasons"])

    def test_n14d_vanguard_rejection_includes_do_not_build_stage9p(self):
        """N14d: rejection_reasons state do not build Stage 9P adapter."""
        rec = _classify_provider("issuer_official_vanguard")
        assert any("stage9p" in r.lower() or "9p" in r.lower() for r in rec["rejection_reasons"])

    def test_n15_schwab_is_manual_research_required(self):
        rec = _classify_provider("issuer_official_schwab")
        assert rec["classification"] == "manual_research_required"

    def test_n16_ssga_is_manual_research_required(self):
        rec = _classify_provider("issuer_official_ssga_spdr")
        assert rec["classification"] == "manual_research_required"

    def test_n16b_vanguard_canonical_ready_false(self):
        rec = _classify_provider("issuer_official_vanguard")
        assert rec["canonical_ready"] is False

    def test_n16c_vanguard_rejection_includes_url_or_404(self):
        rec = _classify_provider("issuer_official_vanguard")
        assert any(
            "url" in r.lower() or "404" in r or "stable" in r.lower()
            for r in rec["rejection_reasons"]
        )

    def test_n16d_vanguard_has_issuer_official_source_authority(self):
        rec = _classify_provider("issuer_official_vanguard")
        assert rec["criteria"]["source_authority"] == "issuer_official"

    def test_n16e_issuer_official_canonical_requires_all_five_criteria(self):
        gate_passed, failures = _check_gate(
            identity_verified=True,
            holdings_count=10,
            weights_available=True,
            as_of_or_report_date_present=True,
            source_authority="issuer_official",
            entitlement_status="free_no_key_required",
        )
        assert gate_passed is True
        assert failures == []

    def test_n16f_vanguard_safe_for_decision_false(self):
        rec = _classify_provider("issuer_official_vanguard")
        assert rec["safe_for_decision"] is False


# ── N17: GLD commodity trust is not_applicable ────────────────────────────────

class TestGldCommodityMatrix:
    def test_n17_classification_is_not_applicable(self):
        rec = _classify_provider("gld_commodity_trust")
        assert rec["classification"] == "not_applicable"

    def test_n17b_canonical_ready_false(self):
        rec = _classify_provider("gld_commodity_trust")
        assert rec["canonical_ready"] is False

    def test_n17c_rejection_includes_commodity_trust(self):
        rec = _classify_provider("gld_commodity_trust")
        assert any("commodity" in r.lower() for r in rec["rejection_reasons"])


# ── N18–N20: S-grade canonical gate invariants ────────────────────────────────

class TestCanonicalGate:
    def test_n18_gate_fails_on_identity_false(self):
        passed, failures = _check_gate(
            identity_verified=False,
            holdings_count=10,
            weights_available=True,
            as_of_or_report_date_present=True,
            source_authority="issuer_official",
            entitlement_status="free_no_key_required",
        )
        assert passed is False
        assert "identity_verified_false" in failures

    def test_n19_gate_fails_on_missing_date(self):
        passed, failures = _check_gate(
            identity_verified=True,
            holdings_count=10,
            weights_available=True,
            as_of_or_report_date_present=False,
            source_authority="sec_primary_authority",
            entitlement_status="free_no_key_required",
        )
        assert passed is False
        assert "as_of_or_report_date_missing" in failures

    def test_n20_gate_fails_on_zero_holdings(self):
        passed, failures = _check_gate(
            identity_verified=True,
            holdings_count=0,
            weights_available=True,
            as_of_or_report_date_present=True,
            source_authority="sec_primary_authority",
            entitlement_status="free_no_key_required",
        )
        assert passed is False
        assert "holdings_count_zero" in failures

    def test_n20b_gate_fails_on_no_weights(self):
        passed, failures = _check_gate(
            identity_verified=True,
            holdings_count=5,
            weights_available=False,
            as_of_or_report_date_present=True,
            source_authority="issuer_official",
            entitlement_status="free_no_key_required",
        )
        assert passed is False
        assert "weights_not_available" in failures

    def test_n20c_gate_fails_on_paywalled_entitlement(self):
        passed, failures = _check_gate(
            identity_verified=True,
            holdings_count=10,
            weights_available=True,
            as_of_or_report_date_present=True,
            source_authority="issuer_official",
            entitlement_status="paywalled_402_on_free_key",
        )
        assert passed is False
        assert "entitlement_not_free" in failures

    def test_n20d_gate_fails_on_editorial_authority(self):
        passed, failures = _check_gate(
            identity_verified=True,
            holdings_count=10,
            weights_available=True,
            as_of_or_report_date_present=True,
            source_authority="third_party_unofficial",
            entitlement_status="free_with_api_key",
        )
        assert passed is False
        assert "source_authority_not_primary_or_issuer" in failures


# ── N21: build_provider_decision_matrix output ────────────────────────────────

class TestBuildMatrix:
    def test_n21_matrix_has_version(self):
        m = _build_matrix()
        assert m["matrix_version"].startswith("stage9n")

    def test_n21b_matrix_has_providers(self):
        m = _build_matrix()
        assert "providers" in m
        assert len(m["providers"]) >= 7

    def test_n21c_matrix_has_etf_class_next_paths(self):
        m = _build_matrix()
        assert "etf_class_next_paths" in m
        assert len(m["etf_class_next_paths"]) >= 5

    def test_n21d_matrix_is_json_serializable(self):
        import json
        m = _build_matrix()
        serialized = json.dumps(m)
        assert len(serialized) > 100

    def test_n21e_patch_loop_stop_reasons_present(self):
        m = _build_matrix()
        stop = m["patch_loop_stop_reasons"]
        assert "sec_nport_vti_schd_vxus" in stop
        assert "alpha_vantage_etf_profile" in stop
        assert "fmp_etf_holdings" in stop
        assert "issuer_official_vanguard" in stop

    def test_n21f_vanguard_stop_reason_mentions_404_and_pivot(self):
        m = _build_matrix()
        stop = m["patch_loop_stop_reasons"]["issuer_official_vanguard"]
        assert "404" in stop
        assert "stage9p" in stop.lower() or "9P" in stop

    def test_n21f_s_grade_criteria_in_matrix(self):
        m = _build_matrix()
        assert "s_grade_criteria" in m
        assert len(m["s_grade_criteria"]) >= 6


# ── N22: Only sec_nport_spy_qqq is canonical_ready ───────────────────────────

class TestOnlySpyQqqCanonical:
    def test_n22_only_spy_qqq_is_canonical_ready(self):
        m = _build_matrix()
        canonical_providers = [
            pid for pid, rec in m["providers"].items()
            if rec["canonical_ready"] is True
        ]
        assert canonical_providers == ["sec_nport_spy_qqq"], (
            f"Expected only sec_nport_spy_qqq as canonical_ready; got {canonical_providers}"
        )


# ── N23: safe_for_decision=False on all providers ─────────────────────────────

class TestSafeForDecisionFalse:
    def test_n23_safe_for_decision_false_everywhere(self):
        m = _build_matrix()
        violators = [
            pid for pid, rec in m["providers"].items()
            if rec["safe_for_decision"] is True
        ]
        assert violators == [], f"safe_for_decision=True on: {violators}"


# ── N24: Stage 9K gate not loosened ───────────────────────────────────────────

class TestStage9KGateUnchanged:
    def test_n24_gate_criteria_count_unchanged(self):
        from app.services.intelligence.research_workers.etf_provider_decision_matrix_v1 import S_GRADE_CRITERIA
        assert len(S_GRADE_CRITERIA) == 6

    def test_n24b_gate_criteria_names_unchanged(self):
        from app.services.intelligence.research_workers.etf_provider_decision_matrix_v1 import S_GRADE_CRITERIA
        expected = {
            "identity_verified",
            "holdings_count_nonzero",
            "weights_available",
            "as_of_or_report_date_present",
            "source_authority_primary_or_issuer",
            "entitlement_status_free",
        }
        assert set(S_GRADE_CRITERIA) == expected


# ── N25: Patch-loop stop reasons ──────────────────────────────────────────────

class TestPatchLoopStopReasons:
    def test_n25_nport_stop_reason_mentions_cik(self):
        m = _build_matrix()
        stop = m["patch_loop_stop_reasons"]
        assert "CIK" in stop["sec_nport_vti_schd_vxus"] or "cik" in stop["sec_nport_vti_schd_vxus"].lower()

    def test_n25b_av_stop_reason_mentions_date(self):
        m = _build_matrix()
        stop = m["patch_loop_stop_reasons"]
        assert "date" in stop["alpha_vantage_etf_profile"].lower()

    def test_n25c_fmp_stop_reason_mentions_402(self):
        m = _build_matrix()
        stop = m["patch_loop_stop_reasons"]
        assert "402" in stop["fmp_etf_holdings"]


# ── N26–N29: ETF class next paths ─────────────────────────────────────────────

class TestEtfClassNextPaths:
    def test_n26_all_etf_classes_present(self):
        required_classes = {
            "vanguard_etfs", "schwab_etfs", "sector_etfs",
            "commodity_etfs", "international_etfs", "standalone_trust_etfs",
        }
        m = _build_matrix()
        assert required_classes.issubset(m["etf_class_next_paths"].keys())

    def test_n27_vanguard_has_no_proven_path_after_stage9o(self):
        """N27: After Stage 9O 404 result, vanguard_etfs recommended_path is not the rejected provider."""
        rec = _get_next_path("vanguard_etfs")
        # recommended_path updated to reflect Stage 9O outcome — no proven path
        assert rec["recommended_path"] != "issuer_official_vanguard"
        assert "blocker" in rec
        assert "404" in rec["blocker"] or "not_proven" in rec["blocker"]

    def test_n27b_vanguard_manual_action_mentions_do_not_build_stage9p(self):
        """N27b: vanguard_etfs manual_action_required warns against Stage 9P adapter."""
        rec = _get_next_path("vanguard_etfs")
        action = rec.get("manual_action_required", "")
        assert "9P" in action or "stage9p" in action.lower() or "9p" in action.lower()

    def test_n28_schwab_recommends_issuer_official_schwab(self):
        rec = _get_next_path("schwab_etfs")
        assert rec["recommended_path"] == "issuer_official_schwab"

    def test_n29_international_etfs_has_blocker(self):
        """N29: international_etfs (VXUS) has a blocker — issuer official URL returned 404."""
        rec = _get_next_path("international_etfs")
        assert "blocker" in rec
        # VXUS is Vanguard — its path is also blocked by Stage 9O 404 result
        assert rec["blocker"] != "none_already_canonical"

    def test_n29b_standalone_trust_recommends_sec_nport(self):
        rec = _get_next_path("standalone_trust_etfs")
        assert rec["recommended_path"] == "sec_nport_spy_qqq"

    def test_n29c_sector_etfs_recommends_ssga(self):
        rec = _get_next_path("sector_etfs")
        assert rec["recommended_path"] == "issuer_official_ssga_spdr"

    def test_n29d_commodity_recommends_commodity_trust(self):
        rec = _get_next_path("commodity_etfs")
        assert rec["recommended_path"] == "gld_commodity_trust"


# ── N30–N31: classify_provider / get_next_path unknowns ──────────────────────

class TestUnknownLookups:
    def test_n30_classify_provider_returns_none_for_unknown(self):
        result = _classify_provider("does_not_exist_v999")
        assert result is None

    def test_n31_get_next_path_returns_none_for_unknown_class(self):
        result = _get_next_path("not_a_real_etf_class")
        assert result is None


# ── N32: Matrix version present ───────────────────────────────────────────────

class TestMatrixVersion:
    def test_n32_matrix_version_is_stage9n(self):
        from app.services.intelligence.research_workers.etf_provider_decision_matrix_v1 import MATRIX_VERSION
        assert MATRIX_VERSION.startswith("stage9n")


# ── N33: S-grade criteria exported ───────────────────────────────────────────

class TestSGradeCriteria:
    def test_n33_criteria_list_exported_as_tuple(self):
        from app.services.intelligence.research_workers.etf_provider_decision_matrix_v1 import S_GRADE_CRITERIA
        assert isinstance(S_GRADE_CRITERIA, tuple)
        assert len(S_GRADE_CRITERIA) > 0


# ── N34–N37: check_canonical_gate independent criteria ───────────────────────

class TestCanonicalGateIndependent:
    _BASE = dict(
        identity_verified=True,
        holdings_count=10,
        weights_available=True,
        as_of_or_report_date_present=True,
        source_authority="issuer_official",
        entitlement_status="free_no_key_required",
    )

    def test_n34_identity_false_fails_alone(self):
        passed, failures = _check_gate(**{**self._BASE, "identity_verified": False})
        assert not passed
        assert "identity_verified_false" in failures

    def test_n35_date_false_fails_alone(self):
        passed, failures = _check_gate(**{**self._BASE, "as_of_or_report_date_present": False})
        assert not passed
        assert "as_of_or_report_date_missing" in failures

    def test_n36_weights_false_fails_alone(self):
        passed, failures = _check_gate(**{**self._BASE, "weights_available": False})
        assert not passed
        assert "weights_not_available" in failures

    def test_n37_all_criteria_true_passes(self):
        passed, failures = _check_gate(**self._BASE)
        assert passed is True
        assert failures == []


# ── N38: AV supplemental classifier regression (Stage 9F.3c) ─────────────────

class TestAvSupplementalClassifierRegression:
    def test_n38_av_classifier_always_canonical_ready_false_without_date(self):
        result = _classify_av(
            holdings_count=100,
            weights_available=True,
            as_of_date=None,
        )
        assert result["canonical_ready"] is False
        assert result["safe_for_decision"] is False
        assert result["supplemental_only"] is True

    def test_n38b_av_classifier_canonical_ready_false_even_with_date(self):
        result = _classify_av(
            holdings_count=100,
            weights_available=True,
            as_of_date="2025-12-31",
        )
        assert result["canonical_ready"] is False


# ── N39: All classifications are defined constants ────────────────────────────

class TestClassificationConstants:
    _VALID = {
        "canonical_ready", "supplemental_only", "manual_research_required",
        "rejected_paywalled", "rejected_insufficient", "not_applicable",
    }

    def test_n39_all_provider_classifications_are_valid(self):
        m = _build_matrix()
        invalid = [
            (pid, rec["classification"])
            for pid, rec in m["providers"].items()
            if rec["classification"] not in self._VALID
        ]
        assert invalid == [], f"Invalid classifications: {invalid}"
