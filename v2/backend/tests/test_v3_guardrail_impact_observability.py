from app.services.intelligence.v3.shadow_projection import (
    summarize_guardrail_impact_observability,
    summarize_shadow_diagnostics,
)


def _diag(action, conviction, guardrail=None):
    d = {"v3_shadow_action": action, "v3_shadow_conviction": conviction}
    if guardrail is not None:
        d["truth_diagnostics"] = {"buy_conviction_guardrail": guardrail}
    return d


def test_empty_diagnostics_safe():
    got = summarize_guardrail_impact_observability([])
    assert got["guardrail_evaluated_count"] == 0
    assert got["buy_high_conviction_pre_guardrail_count"] == 0
    assert got["buy_conviction_capped_count"] == 0


def test_guardrail_impact_counts_and_reasons_deterministic():
    diagnostics = [
        _diag("BUY", "MEDIUM", {
            "buy_high_conviction_guardrail_applied": True,
            "buy_conviction_capped_reason": "evidence_quality_not_high_trust:present:medium",
            "evidence_quality_truth_status": "present",
            "evidence_quality_trust_level": "medium",
            "pre_guardrail_conviction": "HIGH",
            "post_guardrail_conviction": "MEDIUM",
        }),
        _diag("BUY", "HIGH", {
            "buy_high_conviction_guardrail_applied": False,
            "buy_conviction_capped_reason": "",
            "evidence_quality_truth_status": "present",
            "evidence_quality_trust_level": "high",
            "pre_guardrail_conviction": None,
            "post_guardrail_conviction": None,
        }),
        _diag("SELL", "MEDIUM", {
            "buy_high_conviction_guardrail_applied": False,
            "buy_conviction_capped_reason": "",
            "evidence_quality_truth_status": "missing",
            "evidence_quality_trust_level": "unknown",
            "pre_guardrail_conviction": None,
            "post_guardrail_conviction": None,
        }),
        None,
    ]
    got = summarize_guardrail_impact_observability(diagnostics)
    assert got["guardrail_evaluated_count"] == 3
    assert got["buy_high_conviction_pre_guardrail_count"] == 1
    assert got["buy_conviction_capped_count"] == 1
    assert got["buy_remained_buy_after_cap_count"] == 1
    assert got["guardrail_applied_reasons"] == {"evidence_quality_not_high_trust:present:medium": 1}
    assert got["evidence_quality_status_counts"] == {"present": 2, "missing": 1}
    assert got["evidence_quality_trust_counts"] == {"medium": 1, "high": 1, "unknown": 1}
    assert got["v3_shadow_action_counts"] == {"BUY": 2, "HOLD": 0, "TRIM": 0, "SELL": 1}
    assert got["v3_shadow_conviction_counts"] == {"LOW": 0, "MEDIUM": 2, "HIGH": 1}


def test_projection_failures_handled_by_shadow_summary():
    diagnostics = [None, _diag("HOLD", "LOW", None)]
    shadow = summarize_shadow_diagnostics(diagnostics, total_cards=2)
    assert shadow["projection_failures"] == 1
