"""Router-level tests for Stage 12D — paycheck plan preview read model.

Tests the pure diagnostic->preview mapping plus cert-gated endpoint
delegation. No live DB, no provider calls, no LLM calls.
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

_CERT_USER = SimpleNamespace(id=uuid4(), email="cert@example.com")
_CERT_SECRET = "test-secret-12d"


def _cert_settings():
    return SimpleNamespace(
        finance_runtime_cert_enabled=True,
        finance_runtime_cert_secret=_CERT_SECRET,
        finance_runtime_cert_user_id=str(_CERT_USER.id),
        finance_runtime_cert_user_email=_CERT_USER.email,
    )


def _ready_diagnostic(vti_first: bool = True) -> dict:
    vti_candidate = {
        "ticker": "VTI",
        "dollar_amount": 1737.5,
        "current_weight_pct": 30.0,
        "target_or_cap_weight_pct": 40.0,
        "gap_pct": 10.0,
        "gap_dollars": 1737.5,
        "classification": "broad_index_etf",
        "conviction": "neutral",
        "confidence": "policy_only",
        "reason_codes": ["etf_floor_not_met", "core_etf_preference", "preferred_vti_over_spy"],
        "is_unknown_ticker": False,
    }
    spy_candidate = {
        "ticker": "SPY",
        "dollar_amount": 1000.0,
        "current_weight_pct": 20.0,
        "target_or_cap_weight_pct": 25.0,
        "gap_pct": 5.0,
        "gap_dollars": 1000.0,
        "classification": "broad_index_etf",
        "conviction": "neutral",
        "confidence": "policy_only",
        "reason_codes": ["broad_index_etf_group_underweight"],
        "is_unknown_ticker": False,
    }
    candidates = [vti_candidate, spy_candidate] if vti_first else [spy_candidate, vti_candidate]

    return {
        "diagnostic_version": "allocation_policy_v1",
        "generated_at": "2026-07-08T00:00:00Z",
        "input": {"cash_to_deploy": 2737.5, "min_trade_amount": 25.0, "max_positions": 5},
        "truth_dependency": {
            "truth_status": "certified",
            "reconciliation_status": "pass",
            "snapshot_portfolio_value": 10000.0,
            "position_derived_market_value": 10000.0,
            "price_coverage_status": "ok",
            "missing_price_tickers": [],
            "stale_price_tickers": [],
            "can_run_policy": True,
            "blockers": [],
        },
        "current_portfolio": {
            "total_market_value": 10000.0,
            "open_position_count": 3,
            "per_ticker": [{"ticker": t, "market_value": 1000.0} for t in ("VTI", "SPY", "QQQ")],
            "group_weights": {},
            "etf_total_weight_pct": 75.0,
        },
        "generated_policy": {
            "policy_version": "conservative_profile_policy_v1",
            "etf_floor_pct": 40.0,
            "current_etf_pct": 50.0,
            "etf_floor_met": False,
            "group_targets": {},
            "caps": {},
            "intel_v3_overlay_used": False,
            "intel_v3_overlay_warning": None,
            "warnings": [],
        },
        "target_vs_current": {"by_group": {}, "by_ticker": {}},
        "next_buy_candidates": candidates,
        "cash_plan": {
            "cash_to_deploy": 2737.5,
            "allocated_cash": 2737.5,
            "unallocated_cash": 0.0,
            "allocation_count": 2,
            "no_buy_reason": None,
        },
        "verdict": {
            "policy_status": "ready",
            "recommendations_trusted": False,
            "numeric_plan_trusted": True,
            "next_required_fix": "No immediate fix required — policy is ready",
        },
    }


def _blocked_diagnostic() -> dict:
    diag = _ready_diagnostic()
    diag["verdict"] = {
        "policy_status": "blocked",
        "recommendations_trusted": False,
        "numeric_plan_trusted": False,
        "next_required_fix": "Resolve blockers: no_portfolio_value_computable",
    }
    diag["next_buy_candidates"] = []
    diag["cash_plan"] = {**diag["cash_plan"], "allocated_cash": 0.0, "unallocated_cash": 2737.5,
                          "allocation_count": 0, "no_buy_reason": "policy_blocked: no_portfolio_value_computable"}
    return diag


def _degraded_diagnostic() -> dict:
    diag = _ready_diagnostic()
    diag["truth_dependency"] = {
        **diag["truth_dependency"],
        "price_coverage_status": "stale",
        "stale_price_tickers": ["VTI"],
    }
    diag["verdict"] = {
        "policy_status": "degraded",
        "recommendations_trusted": False,
        "numeric_plan_trusted": False,
        "next_required_fix": "Run Stage 11B current-price-truth-repair to refresh stale price_history rows",
    }
    return diag


# ── Mapping (pure function) tests ─────────────────────────────────────────────

def test_ready_diagnostic_maps_to_ready_preview():
    from app.routers.paycheck_plan_preview import build_paycheck_plan_preview

    preview = build_paycheck_plan_preview(_ready_diagnostic())
    assert preview["status"] == "ready"
    assert preview["preview_version"] == "paycheck_plan_preview_v1"
    assert preview["source_diagnostic_version"] == "allocation_policy_v1"


def test_trusted_mirrors_numeric_plan_trusted():
    from app.routers.paycheck_plan_preview import build_paycheck_plan_preview

    ready = build_paycheck_plan_preview(_ready_diagnostic())
    assert ready["trusted"] is True

    blocked = build_paycheck_plan_preview(_blocked_diagnostic())
    assert blocked["trusted"] is False

    degraded = build_paycheck_plan_preview(_degraded_diagnostic())
    assert degraded["trusted"] is False


def test_recommendations_trusted_always_false():
    from app.routers.paycheck_plan_preview import build_paycheck_plan_preview

    for diag in (_ready_diagnostic(), _blocked_diagnostic(), _degraded_diagnostic()):
        preview = build_paycheck_plan_preview(diag)
        assert preview["recommendations_trusted"] is False


def test_blocked_is_not_ready_and_not_actionable():
    from app.routers.paycheck_plan_preview import build_paycheck_plan_preview

    blocked = build_paycheck_plan_preview(_blocked_diagnostic())
    assert blocked["status"] == "blocked"
    assert blocked["planned_buys"] == []


def test_degraded_but_calculable_preserves_the_computed_plan():
    """Degraded-but-calculable rule (Deploy Cash product recovery): a stale
    price on some OTHER holding must not erase a real, already-priced plan
    the diagnostic computed. The plan stays untrusted/degraded but its
    dollar amounts are preserved rather than replaced with an empty list."""
    from app.routers.paycheck_plan_preview import build_paycheck_plan_preview

    degraded = build_paycheck_plan_preview(_degraded_diagnostic())
    assert degraded["status"] == "degraded"
    # numeric_plan_trusted False must never yield a "ready" status
    assert degraded["status"] != "ready"
    assert degraded["trusted"] is False
    assert [b["ticker"] for b in degraded["planned_buys"]] == ["VTI", "SPY"]


def test_planned_buys_output_is_concise_no_raw_diagnostic_payload():
    from app.routers.paycheck_plan_preview import build_paycheck_plan_preview

    preview = build_paycheck_plan_preview(_ready_diagnostic())
    assert "next_buy_candidates" not in preview
    assert "current_portfolio" not in preview
    assert "target_vs_current" not in preview
    assert "truth_dependency" not in preview

    for buy in preview["planned_buys"]:
        assert set(buy.keys()) == {"ticker", "amount", "reason", "reason_codes"}
        assert isinstance(buy["reason"], str) and buy["reason"]


def test_reason_codes_preserved_with_plain_english_reason():
    from app.routers.paycheck_plan_preview import build_paycheck_plan_preview

    preview = build_paycheck_plan_preview(_ready_diagnostic())
    vti_buy = next(b for b in preview["planned_buys"] if b["ticker"] == "VTI")
    assert "core_etf_preference" in vti_buy["reason_codes"]
    assert "preferred_vti_over_spy" in vti_buy["reason_codes"]
    assert "etf_floor_not_met" in vti_buy["reason_codes"]
    assert "ETF" in vti_buy["reason"] or "core" in vti_buy["reason"].lower()

    spy_buy = next(b for b in preview["planned_buys"] if b["ticker"] == "SPY")
    assert "broad_index_etf_group_underweight" in spy_buy["reason_codes"]
    assert "underweight" in spy_buy["reason"].lower()


def test_cash_invariant_fields_pass_through():
    from app.routers.paycheck_plan_preview import build_paycheck_plan_preview

    preview = build_paycheck_plan_preview(_ready_diagnostic())
    summary = preview["allocation_summary"]
    assert summary["allocated_cash"] <= preview["cash_to_deploy"]
    assert summary["unallocated_cash"] >= 0
    assert summary["allocation_count"] == 2


def test_vti_first_ordering_preserved():
    from app.routers.paycheck_plan_preview import build_paycheck_plan_preview

    preview = build_paycheck_plan_preview(_ready_diagnostic(vti_first=True))
    assert preview["planned_buys"][0]["ticker"] == "VTI"


def test_caveats_state_not_personalized_advice():
    from app.routers.paycheck_plan_preview import build_paycheck_plan_preview

    preview = build_paycheck_plan_preview(_ready_diagnostic())
    assert any("not personalized investment advice" in c for c in preview["caveats"])


def test_next_required_fix_none_when_ready_and_trusted():
    from app.routers.paycheck_plan_preview import build_paycheck_plan_preview

    preview = build_paycheck_plan_preview(_ready_diagnostic())
    assert preview["next_required_fix"] is None


def test_next_required_fix_populated_when_blocked():
    from app.routers.paycheck_plan_preview import build_paycheck_plan_preview

    preview = build_paycheck_plan_preview(_blocked_diagnostic())
    assert preview["next_required_fix"] is not None


# ── Endpoint delegation tests (read-only, no writes/provider/LLM calls) ───────

@pytest.mark.asyncio
async def test_endpoint_delegates_to_stage_12c_service_and_is_read_only(monkeypatch):
    from app.routers.paycheck_plan_preview import (
        PaycheckPlanPreviewRequest,
        paycheck_plan_preview,
    )

    calls: dict = {}

    async def _fake_run(**kwargs):
        calls.update(kwargs)
        return _ready_diagnostic()

    monkeypatch.setattr("app.routers.diagnostics.get_settings", lambda: _cert_settings())
    monkeypatch.setattr("app.routers.paycheck_plan_preview.get_supabase_client", lambda: SimpleNamespace())
    monkeypatch.setattr(
        "app.services.allocation_policy_v1.run_next_buy_policy_diagnostic",
        _fake_run,
    )

    result = await paycheck_plan_preview(
        payload=PaycheckPlanPreviewRequest(cash_to_deploy=2737.5, max_positions=5, min_trade_amount=25.0),
        user=_CERT_USER,
    )

    # Delegated to the Stage 12C service with the same inputs — no duplicated math.
    assert calls["cash_to_deploy"] == 2737.5
    assert calls["max_positions"] == 5
    assert calls["min_trade_amount"] == 25.0

    assert result["status"] == "ready"
    assert result["trusted"] is True
    assert result["recommendations_trusted"] is False
    assert result["planned_buys"][0]["ticker"] == "VTI"
    assert result["allocation_summary"]["allocated_cash"] <= result["cash_to_deploy"]
    assert result["allocation_summary"]["unallocated_cash"] >= 0
    assert "next_buy_candidates" not in result
    assert "current_portfolio" not in result


# ── Consolidation: plan explanation buckets (Advisor cash-plan section) ───────


def _evidence_aware_diagnostic() -> dict:
    """Ready diagnostic with one selected stock and several blocked tickers,
    mirroring the Stage 13A/13C production gate fields."""
    diag = _ready_diagnostic()
    diag["next_buy_candidates"] = diag["next_buy_candidates"] + [{
        "ticker": "NVDA",
        "dollar_amount": 500.0,
        "gap_pct": 3.0,
        "classification": "individual_stock",
        "asset_type": "equity",
        "candidate_source": "evidence_aware_stock_ranking_v1",
        "confidence_label": "high_confidence_evidence",
        "reason_codes": ["evidence_fresh_and_constructive", "positive_gap"],
        "is_unknown_ticker": False,
    }]
    diag["cash_plan"] = {**diag["cash_plan"], "allocated_cash": 3237.5, "allocation_count": 3}
    diag["input"] = {**diag["input"], "cash_to_deploy": 3237.5}
    diag["target_vs_current"]["by_ticker"] = {
        "MSFT": {
            "ticker": "MSFT", "group": "individual_stock",
            "gap_pct": 2.0, "eligible_for_buy": False,
            "ineligibility_reason": "individual_stock_group_above_target",
            "policy_ineligibility_reason": "individual_stock_group_above_target",
            "evidence_gate_passed": True, "evidence_gate_codes": [],
        },
        "CRM": {
            "ticker": "CRM", "group": "individual_stock",
            "gap_pct": 1.0, "eligible_for_buy": False,
            "ineligibility_reason": "evidence_gate_failed:evidence_signal_not_constructive",
            "policy_ineligibility_reason": None,
            "evidence_gate_passed": False,
            "evidence_gate_codes": ["evidence_signal_not_constructive"],
        },
        "QQQ": {
            "ticker": "QQQ", "group": "broad_index_etf",
            "gap_pct": -1.0, "eligible_for_buy": False,
            "ineligibility_reason": "etf_group_broad_index_etf_already_above_target",
            "policy_ineligibility_reason": "etf_group_broad_index_etf_already_above_target",
            "evidence_gate_passed": None, "evidence_gate_codes": [],
        },
    }
    diag["stock_candidates"] = {
        "status": "enabled",
        "held_tickers": ["NVDA", "MSFT", "CRM"],
        "selected_tickers": ["NVDA"],
        "blocked_by_evidence_tickers": ["CRM"],
        "blocked_by_policy_tickers": ["MSFT"],
        "evidence_eligible_but_policy_blocked_tickers": ["MSFT"],
        "policy_block_reason_codes": {"MSFT": "individual_stock_group_above_target"},
    }
    return diag


def test_preview_includes_generated_at_and_explanations_keys():
    from app.routers.paycheck_plan_preview import build_paycheck_plan_preview

    preview = build_paycheck_plan_preview(_ready_diagnostic())
    assert preview["generated_at"] == "2026-07-08T00:00:00Z"
    assert set(preview["explanations"].keys()) == {"selected", "not_selected", "plan_notes"}


def test_selected_entries_carry_amount_percent_reasons_and_role():
    from app.routers.paycheck_plan_preview import build_paycheck_plan_preview

    preview = build_paycheck_plan_preview(_ready_diagnostic())
    sel = preview["explanations"]["selected"]
    assert [e["ticker"] for e in sel] == ["VTI", "SPY"]
    vti = sel[0]
    assert vti["amount"] == 1737.5
    assert vti["percent_of_deployable_cash"] == round(100 * 1737.5 / 2737.5, 2)
    assert vti["policy_role"] == "Fills the 40% ETF allocation floor"
    assert vti["raw_codes"] == ["etf_floor_not_met", "core_etf_preference", "preferred_vti_over_spy"]
    assert all(isinstance(r, str) and "_" not in r[:1] for r in vti["reasons"])


def test_selected_stock_carries_evidence_action_and_band():
    from app.routers.paycheck_plan_preview import build_paycheck_plan_preview

    preview = build_paycheck_plan_preview(_evidence_aware_diagnostic())
    nvda = next(e for e in preview["explanations"]["selected"] if e["ticker"] == "NVDA")
    assert nvda["evidence"] == {"action": "BUY", "evidence_band": "STRONG"}
    assert nvda["asset_type"] == "equity"
    assert nvda["policy_role"] is None


def test_evidence_eligible_but_policy_blocked_bucket():
    from app.routers.paycheck_plan_preview import build_paycheck_plan_preview

    preview = build_paycheck_plan_preview(_evidence_aware_diagnostic())
    msft = next(e for e in preview["explanations"]["not_selected"] if e["ticker"] == "MSFT")
    assert msft["bucket"] == "evidence_eligible_policy_blocked"
    assert "passed Intel evidence" in msft["plain_english"]
    assert "individual_stock_group_above_target" in msft["raw_codes"]


def test_evidence_blocked_bucket_translates_hold_gate():
    from app.routers.paycheck_plan_preview import build_paycheck_plan_preview

    preview = build_paycheck_plan_preview(_evidence_aware_diagnostic())
    crm = next(e for e in preview["explanations"]["not_selected"] if e["ticker"] == "CRM")
    assert crm["bucket"] == "evidence_blocked"
    assert "HOLD" in crm["plain_english"]
    assert "evidence_signal_not_constructive" in crm["raw_codes"]


def test_group_cap_blocked_bucket_for_etf_above_target():
    from app.routers.paycheck_plan_preview import build_paycheck_plan_preview

    preview = build_paycheck_plan_preview(_evidence_aware_diagnostic())
    qqq = next(e for e in preview["explanations"]["not_selected"] if e["ticker"] == "QQQ")
    assert qqq["bucket"] == "group_cap_blocked"
    assert "above its target" in qqq["plain_english"]


def test_etf_only_plan_note_when_stocks_policy_blocked():
    from app.routers.paycheck_plan_preview import build_paycheck_plan_preview

    diag = _evidence_aware_diagnostic()
    diag["next_buy_candidates"] = [c for c in diag["next_buy_candidates"] if c["ticker"] != "NVDA"]
    diag["stock_candidates"] = {
        **diag["stock_candidates"],
        "status": "blocked_by_policy_caps",
        "selected_tickers": [],
    }
    preview = build_paycheck_plan_preview(diag)
    notes = " ".join(preview["explanations"]["plan_notes"])
    assert "ETF-only" in notes
    assert "individual-stock sleeve is already above its policy target" in notes


def test_etf_only_plan_note_when_evidence_insufficient():
    from app.routers.paycheck_plan_preview import build_paycheck_plan_preview

    diag = _ready_diagnostic()
    diag["stock_candidates"] = {
        "status": "blocked_insufficient_evidence",
        "held_tickers": ["NVDA"], "selected_tickers": [],
        "blocked_by_evidence_tickers": ["NVDA"],
        "blocked_by_policy_tickers": [],
        "evidence_eligible_but_policy_blocked_tickers": [],
        "policy_block_reason_codes": {},
    }
    preview = build_paycheck_plan_preview(diag)
    notes = " ".join(preview["explanations"]["plan_notes"])
    assert "ETF-only" in notes and "evidence" in notes


def test_stale_and_missing_price_buckets():
    from app.routers.paycheck_plan_preview import build_paycheck_plan_preview

    diag = _degraded_diagnostic()
    diag["truth_dependency"]["missing_price_tickers"] = ["KLAR"]
    preview = build_paycheck_plan_preview(diag)
    buckets = {e["ticker"]: e["bucket"] for e in preview["explanations"]["not_selected"]}
    # VTI is stale-priced in this fixture but is still one of the diagnostic's
    # own next_buy_candidates (a *different* held ticker carries the stale
    # flag) — the stale-price bucket documents the caveat without erasing
    # VTI's own selected/priced entry.
    assert buckets.get("KLAR") == "missing_truth_blocked"
    assert [e["ticker"] for e in preview["explanations"]["selected"]] == ["VTI", "SPY"]


def test_max_positions_reached_bucket():
    from app.routers.paycheck_plan_preview import build_paycheck_plan_preview

    diag = _ready_diagnostic()
    diag["input"] = {**diag["input"], "max_positions": 2}
    diag["target_vs_current"]["by_ticker"] = {
        "SCHD": {
            "ticker": "SCHD", "group": "dividend_etf", "gap_pct": 2.0,
            "eligible_for_buy": True, "ineligibility_reason": None,
            "policy_ineligibility_reason": None,
            "evidence_gate_passed": None, "evidence_gate_codes": [],
        },
    }
    preview = build_paycheck_plan_preview(diag)
    schd = next(e for e in preview["explanations"]["not_selected"] if e["ticker"] == "SCHD")
    assert schd["bucket"] == "max_positions_reached"
    assert "maximum of 2 positions" in schd["plain_english"]


def test_below_minimum_trade_bucket():
    from app.routers.paycheck_plan_preview import build_paycheck_plan_preview

    diag = _ready_diagnostic()
    diag["target_vs_current"]["by_ticker"] = {
        "SCHD": {
            "ticker": "SCHD", "group": "dividend_etf", "gap_pct": 2.0,
            "eligible_for_buy": True, "ineligibility_reason": None,
            "policy_ineligibility_reason": None,
            "evidence_gate_passed": None, "evidence_gate_codes": [],
        },
    }
    preview = build_paycheck_plan_preview(diag)
    schd = next(e for e in preview["explanations"]["not_selected"] if e["ticker"] == "SCHD")
    assert schd["bucket"] == "below_minimum_trade"
    assert "$25 minimum trade" in schd["plain_english"]


def test_no_eligible_candidates_note():
    from app.routers.paycheck_plan_preview import build_paycheck_plan_preview

    diag = _ready_diagnostic()
    diag["next_buy_candidates"] = []
    diag["cash_plan"] = {**diag["cash_plan"], "allocated_cash": 0.0,
                          "allocation_count": 0,
                          "no_buy_reason": "no_eligible_buy_candidates"}
    preview = build_paycheck_plan_preview(diag)
    notes = " ".join(preview["explanations"]["plan_notes"])
    assert "No holding is currently eligible" in notes


def test_explanations_never_expose_untranslated_codes_in_plain_english():
    from app.routers.paycheck_plan_preview import build_paycheck_plan_preview

    preview = build_paycheck_plan_preview(_evidence_aware_diagnostic())
    for entry in preview["explanations"]["not_selected"]:
        # Raw codes live only in raw_codes; the visible sentence is prose.
        assert "policy_ineligibility_reason" not in entry["plain_english"]
        assert not entry["plain_english"].startswith("evidence_")
        assert entry["raw_codes"] is not None


def test_existing_stage_12d_contract_keys_unchanged():
    from app.routers.paycheck_plan_preview import build_paycheck_plan_preview

    preview = build_paycheck_plan_preview(_ready_diagnostic())
    for key in ("preview_version", "cash_to_deploy", "trusted", "status", "planned_buys",
                "allocation_summary", "data_freshness_status", "caveats",
                "next_required_fix", "recommendations_trusted", "source_diagnostic_version"):
        assert key in preview
    for buy in preview["planned_buys"]:
        assert set(buy.keys()) == {"ticker", "amount", "reason", "reason_codes"}
