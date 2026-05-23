"""Stage 8F — SEC filing type display adapter tests.

Verifies that:
- build_filing_type_display() maps known form types to plain-English labels
- 10-K → "Annual report (10-K)"
- 10-Q → "Quarterly report (10-Q)"
- 8-K → "Company event filing (8-K)"
- Multiple distinct form types → "Multiple recent official filings"
- Unknown/unrecognised forms only → "Official company filing"
- Empty list → {} (no label — caller falls back to Stage 8E copy)
- Raw codes never leak into display strings (form code appears only after label)
- No decision authority claims
- Stage 8F contract marker / staleness helpers work correctly
- ETF/non-equity hidden state: unchanged (sec_lane_applicable=false, handled by 8D layer)
"""
from __future__ import annotations

import pytest

from app.services.intelligence.v3.sec_filing_type_adapter_v1 import (
    build_filing_type_display,
    _FORM_TYPE_DISPLAY,
    _GENERIC_LABEL,
    _MULTIPLE_LABEL,
)
from app.services.intelligence.v3.stage8f_filing_type_contract_v1 import (
    STAGE8F_FILING_TYPE_CONTRACT_VERSION,
    is_snapshot_stage8f_complete,
)

# ── Raw-code leak guard ────────────────────────────────────────────────────────
# The form codes (10-K, 10-Q, 8-K) are ALLOWED to appear in output only when
# preceded by a plain-English label.  Standalone backend pipeline codes must not.
_FORBIDDEN_BACKEND_CODES = {
    "sec_catalyst_sentiment",
    "skill_pack",
    "fact_kind",
    "READY",
    "PARTIAL",
    "LIMITED",
    "USABLE_WITH_LIMITATIONS",
    "SUPPRESSED_INCOMPLETE",
    "stage8c_sec_catalyst_sentiment",
    "sec_catalyst_sentiment_evidence_v1",
}


def _assert_no_backend_codes(text: str) -> None:
    for code in _FORBIDDEN_BACKEND_CODES:
        assert code not in text, f"Backend code '{code}' leaked: {text!r}"


def _assert_no_decision_authority(text: str) -> None:
    lower = text.lower()
    for phrase in ("buy", "sell", "trim", "hold", "recommendation"):
        assert phrase not in lower, f"Decision authority phrase '{phrase}' leaked: {text!r}"


# ── Core mapping tests ─────────────────────────────────────────────────────────

class TestBuildFilingTypeDisplay:
    def test_10k_returns_annual_report(self):
        result = build_filing_type_display(["10-K"])
        assert result == {"filing_type_label": "Annual report (10-K)"}

    def test_10q_returns_quarterly_report(self):
        result = build_filing_type_display(["10-Q"])
        assert result == {"filing_type_label": "Quarterly report (10-Q)"}

    def test_8k_returns_company_event_filing(self):
        result = build_filing_type_display(["8-K"])
        assert result == {"filing_type_label": "Company event filing (8-K)"}

    def test_multiple_distinct_forms_returns_multiple_label(self):
        result = build_filing_type_display(["10-K", "10-Q"])
        assert result == {"filing_type_label": _MULTIPLE_LABEL}

    def test_multiple_with_8k_returns_multiple_label(self):
        result = build_filing_type_display(["10-Q", "8-K"])
        assert result == {"filing_type_label": _MULTIPLE_LABEL}

    def test_all_three_forms_returns_multiple_label(self):
        result = build_filing_type_display(["10-K", "10-Q", "8-K"])
        assert result == {"filing_type_label": _MULTIPLE_LABEL}

    def test_duplicates_of_same_form_treated_as_single(self):
        # Two sources with same 10-K — one company filed one 10-K.
        result = build_filing_type_display(["10-K", "10-K"])
        assert result == {"filing_type_label": "Annual report (10-K)"}

    def test_unknown_form_returns_generic_label(self):
        result = build_filing_type_display(["SC 13G"])
        assert result == {"filing_type_label": _GENERIC_LABEL}

    def test_unknown_forms_only_returns_generic_label(self):
        result = build_filing_type_display(["DEF 14A", "S-1"])
        assert result == {"filing_type_label": _GENERIC_LABEL}

    def test_empty_list_returns_empty_dict(self):
        assert build_filing_type_display([]) == {}

    def test_none_entries_ignored(self):
        # section_reference can be None if DB row lacks it.
        result = build_filing_type_display([None, None])  # type: ignore[list-item]
        assert result == {}

    def test_empty_strings_ignored(self):
        result = build_filing_type_display(["", "   "])
        assert result == {}

    def test_case_normalisation_lowercase_accepted(self):
        result = build_filing_type_display(["10-k"])
        assert result == {"filing_type_label": "Annual report (10-K)"}

    def test_case_normalisation_mixed_case(self):
        result = build_filing_type_display(["10-Q", "8-k"])
        assert result == {"filing_type_label": _MULTIPLE_LABEL}

    def test_mixed_known_and_unknown_forms(self):
        # Known form present — should surface the known label (not collapse to generic).
        result = build_filing_type_display(["10-K", "SC 13G"])
        # 10-K is known, SC 13G is unknown → only one known → single label
        assert result == {"filing_type_label": "Annual report (10-K)"}

    def test_mixed_known_forms_with_unknown_deduped(self):
        # Multiple known forms plus unknown
        result = build_filing_type_display(["10-K", "10-Q", "S-1"])
        assert result == {"filing_type_label": _MULTIPLE_LABEL}


# ── Raw-code leak guard ────────────────────────────────────────────────────────

class TestNoRawCodeLeak:
    @pytest.mark.parametrize("form_types", [
        ["10-K"], ["10-Q"], ["8-K"], ["10-K", "10-Q"], ["SC 13G"], [], ["10-K", "10-K"],
    ])
    def test_no_backend_codes_in_label(self, form_types):
        result = build_filing_type_display(form_types)
        label = result.get("filing_type_label", "")
        _assert_no_backend_codes(label)

    @pytest.mark.parametrize("form_types", [
        ["10-K"], ["10-Q"], ["8-K"], ["10-K", "8-K"], ["DEF 14A"],
    ])
    def test_no_decision_authority_phrases(self, form_types):
        result = build_filing_type_display(form_types)
        label = result.get("filing_type_label", "")
        _assert_no_decision_authority(label)

    def test_form_code_appears_after_plain_label(self):
        # Ensure form code is parenthetical — never the leading token.
        result = build_filing_type_display(["10-K"])
        label = result["filing_type_label"]
        # Plain English label must come before the code.
        assert label.startswith("Annual report"), f"Code leads label: {label!r}"
        # Code in parentheses is allowed.
        assert "(10-K)" in label

    def test_10q_code_after_plain_label(self):
        result = build_filing_type_display(["10-Q"])
        label = result["filing_type_label"]
        assert label.startswith("Quarterly report")
        assert "(10-Q)" in label

    def test_8k_code_after_plain_label(self):
        result = build_filing_type_display(["8-K"])
        label = result["filing_type_label"]
        assert label.startswith("Company event filing")
        assert "(8-K)" in label


# ── Stage 8F contract staleness helpers ───────────────────────────────────────

class TestStage8fContract:
    def test_contract_version_is_non_empty_string(self):
        assert isinstance(STAGE8F_FILING_TYPE_CONTRACT_VERSION, str)
        assert len(STAGE8F_FILING_TYPE_CONTRACT_VERSION) > 0

    def test_missing_payload_returns_false(self):
        assert is_snapshot_stage8f_complete(None) is False

    def test_empty_payload_returns_false(self):
        assert is_snapshot_stage8f_complete({}) is False

    def test_wrong_version_returns_false(self):
        payload = {"stage8f_filing_type_contract_version": "stage8f_old_v0"}
        assert is_snapshot_stage8f_complete(payload) is False

    def test_correct_version_returns_true(self):
        payload = {
            "stage8f_filing_type_contract_version": STAGE8F_FILING_TYPE_CONTRACT_VERSION
        }
        assert is_snapshot_stage8f_complete(payload) is True

    def test_missing_version_key_returns_false(self):
        payload = {"stage8e_catalyst_explanation_contract_version": "stage8e_catalyst_explanation_v1"}
        assert is_snapshot_stage8f_complete(payload) is False

    def test_none_version_value_returns_false(self):
        payload = {"stage8f_filing_type_contract_version": None}
        assert is_snapshot_stage8f_complete(payload) is False

    def test_full_snapshot_payload_with_correct_marker_returns_true(self):
        payload = {
            "evidence_mapping_version": "analyst_verdict_synthesis_v1",
            "stage7_explanation_contract_version": "stage7_explanation_v2",
            "stage8e_catalyst_explanation_contract_version": "stage8e_catalyst_explanation_v1",
            "stage8f_filing_type_contract_version": STAGE8F_FILING_TYPE_CONTRACT_VERSION,
            "current_holdings": [],
        }
        assert is_snapshot_stage8f_complete(payload) is True


# ── ETF / non-equity hidden state unchanged ────────────────────────────────────

class TestEtfHiddenStateUnchanged:
    """Stage 8F does not touch sec_lane_applicable — ETF/crypto suppression is
    owned by Stage 8D (catalyst_display_adapter_v1). This test verifies that
    the filing_type_display adapter itself never fabricates a label for an ETF
    context — it only maps what form_types are passed to it."""

    def test_empty_form_types_returns_no_label(self):
        # ETF/crypto path: sec_lane_applicable=False, no sources written.
        # Caller passes [] → no filing_type_label → correct.
        assert build_filing_type_display([]) == {}

    def test_adapter_is_pure_and_ignores_asset_type(self):
        # The adapter has no asset-type awareness; classification is upstream.
        # Passing form types produces a label regardless — the caller is
        # responsible for only calling this when sec_lane_applicable=True.
        result = build_filing_type_display(["10-K"])
        assert "filing_type_label" in result


# ── Form-type map completeness ─────────────────────────────────────────────────

class TestFormTypeMapCompleteness:
    def test_10k_in_form_type_display_map(self):
        assert "10-K" in _FORM_TYPE_DISPLAY

    def test_10q_in_form_type_display_map(self):
        assert "10-Q" in _FORM_TYPE_DISPLAY

    def test_8k_in_form_type_display_map(self):
        assert "8-K" in _FORM_TYPE_DISPLAY

    def test_all_map_values_are_tuples_of_two_strings(self):
        for form, (label, code) in _FORM_TYPE_DISPLAY.items():
            assert isinstance(label, str) and label
            assert isinstance(code, str) and code

    def test_no_raw_codes_in_map_plain_labels(self):
        for _form, (label, _code) in _FORM_TYPE_DISPLAY.items():
            _assert_no_backend_codes(label)
            _assert_no_decision_authority(label)
