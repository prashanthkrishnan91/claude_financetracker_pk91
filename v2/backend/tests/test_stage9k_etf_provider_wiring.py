"""Stage 9K — Wire safe ETF provider evidence into Intel card metadata.

Tests that:
  - NPORT artifact payload fields are correctly extracted into an nport_output dict.
  - Only holdings-ready payloads (fetch_status=success, holdings_count≥5,
    weights_available, report_period_date present) reach card_meta["etf_provider_outputs"].
  - When etf_provider_outputs flows into card_meta, the snapshot_builder produces
    context text referencing "full holdings data available" (not "not yet wired").
  - ETFs without qualifying NPORT artifacts keep honest "not yet wired" text.
  - AV missing-date output stays profile_ready (not holdings_ready).
  - FMP 402/paywalled stays not-ready.
  - VTI/SCHD/VXUS retain ETF lens; GLD retains commodity hedge lens.
  - Stocks and crypto are unaffected.
  - Visible action is always preserved; safe_for_decision and synthesis_ready
    are always False.

Fixture-based only. No IO, no DB, no LLM, no SQL.
"""
from __future__ import annotations

import pytest

from app.services.intelligence.v3.intel_context_adapter_v1 import build_intel_context
from app.services.intelligence.v3.asset_intelligence_composer_v1 import (
    LENS_ETF_ROLE,
    LENS_COMMODITY_HEDGE,
    LENS_STOCK_FUNDAMENTAL,
    LENS_CRYPTO,
    FIT_UNKNOWN,
    FIT_UNDERWEIGHT,
    FIT_ON_TARGET,
)
from app.services.intelligence.v3.etf_intelligence_classifier_v1 import (
    ETF_TIER_HOLDINGS_READY,
    ETF_TIER_PROFILE_READY,
    ETF_TIER_METADATA_ONLY,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _etf_ctx(
    ticker: str,
    fit: str = FIT_UNKNOWN,
    eq: str = "OK",
    action: str = "HOLD",
    provider_outputs: dict | None = None,
    upstream_signals: dict | None = None,
) -> dict | None:
    return build_intel_context(
        ticker=ticker,
        asset_type="etf",
        portfolio_fit_raw=fit,
        evidence_quality_raw=eq,
        existing_action=action,
        provider_outputs=provider_outputs,
        upstream_signals=upstream_signals,
    )


def _stock_ctx(ticker: str, fit: str = FIT_UNKNOWN, eq: str = "OK") -> dict | None:
    return build_intel_context(
        ticker=ticker,
        asset_type="stock",
        portfolio_fit_raw=fit,
        evidence_quality_raw=eq,
        existing_action="HOLD",
    )


def _crypto_ctx(ticker: str) -> dict | None:
    return build_intel_context(
        ticker=ticker,
        asset_type="crypto",
        portfolio_fit_raw=FIT_UNKNOWN,
        evidence_quality_raw="OK",
        existing_action="HOLD",
    )


def _make_decision(
    ticker: str = "TEST",
    action: str = "HOLD",
    fit: str = "UNKNOWN",
    eq: str = "OK",
):
    from app.services.intelligence.v3.decision_contracts import (
        ActionV3, AxisBand, ConvictionV3, DecisionOutputV3,
        FitBand, RiskBand, PriceBand,
    )
    return DecisionOutputV3(
        ticker=ticker,
        action=ActionV3[action],
        conviction=ConvictionV3.MEDIUM,
        evidence_quality=AxisBand[eq],
        portfolio_fit=FitBand[fit],
        risk_band=RiskBand.LOW,
        price_context=PriceBand.SUPPRESSED,
        attractiveness=AxisBand.OK,
        rationale_plain_english="Test rationale.",
        why_now="",
        why_not_now="",
        blockers=[],
        suppression_reasons={},
        source_signal_summary={},
        schema_version="v3.1",
    )


def _snap_card(
    ticker: str,
    category: str,
    action: str = "HOLD",
    fit: str = "UNKNOWN",
    eq: str = "OK",
    extra_meta: dict | None = None,
) -> dict:
    from app.services.intelligence.v3.snapshot_builder import build_snapshot
    decision = _make_decision(ticker=ticker, action=action, fit=fit, eq=eq)
    meta: dict = {"ticker": ticker, "name": ticker, "category": category}
    if extra_meta:
        meta.update(extra_meta)
    snap = build_snapshot(run_id="test-9k", decisions=[decision], card_metas=[meta])
    return snap["current_holdings"][0]


# ── NPORT payload extraction gate tests ───────────────────────────────────────


class TestNportPayloadGate:
    """Verify which NPORT artifact payloads pass the holdings-ready gate.

    These test the payload field shape that _get_etf_nport_provider_outputs()
    would return, mirroring the gate logic in the service method (including
    coverage_quality — partial/suspicious values must be blocked).
    """

    @staticmethod
    def _gate(payload: dict) -> bool:
        """Mirror the in-service gate for testability (including coverage_quality)."""
        nport_out = {
            "fetch_status": payload.get("fetch_status", ""),
            "holdings_count": payload.get("holdings_count", 0) or 0,
            "weights_available": bool(payload.get("weights_available", False)),
            "report_period_date": payload.get("report_period_date"),
            "coverage_quality": payload.get("coverage_quality") or "",
        }
        fetch_ok = (nport_out["fetch_status"] or "").lower() == "success"
        count_ok = nport_out["holdings_count"] >= 5
        weights_ok = nport_out["weights_available"]
        date_ok = bool(nport_out["report_period_date"])
        cq = (nport_out["coverage_quality"] or "").lower()
        coverage_ok = "partial" not in cq and "suspicious" not in cq
        return fetch_ok and count_ok and weights_ok and date_ok and coverage_ok

    def test_full_holdings_ready_payload_passes(self):
        payload = {
            "fetch_status": "success",
            "holdings_count": 103,
            "weights_available": True,
            "report_period_date": "2025-12-31",
        }
        assert self._gate(payload) is True

    def test_missing_date_fails_gate(self):
        payload = {
            "fetch_status": "success",
            "holdings_count": 103,
            "weights_available": True,
            "report_period_date": None,
        }
        assert self._gate(payload) is False

    def test_empty_date_string_fails_gate(self):
        payload = {
            "fetch_status": "success",
            "holdings_count": 103,
            "weights_available": True,
            "report_period_date": "",
        }
        assert self._gate(payload) is False

    def test_bad_fetch_status_fails_gate(self):
        for status in ("no_filing", "parse_error", "error", "timeout", ""):
            payload = {
                "fetch_status": status,
                "holdings_count": 103,
                "weights_available": True,
                "report_period_date": "2025-12-31",
            }
            assert self._gate(payload) is False, f"Expected False for status={status!r}"

    def test_low_holdings_count_fails_gate(self):
        payload = {
            "fetch_status": "success",
            "holdings_count": 4,
            "weights_available": True,
            "report_period_date": "2025-12-31",
        }
        assert self._gate(payload) is False

    def test_zero_holdings_count_fails_gate(self):
        payload = {
            "fetch_status": "success",
            "holdings_count": 0,
            "weights_available": True,
            "report_period_date": "2025-12-31",
        }
        assert self._gate(payload) is False

    def test_weights_not_available_fails_gate(self):
        payload = {
            "fetch_status": "success",
            "holdings_count": 103,
            "weights_available": False,
            "report_period_date": "2025-12-31",
        }
        assert self._gate(payload) is False

    def test_minimum_passing_case(self):
        payload = {
            "fetch_status": "success",
            "holdings_count": 5,
            "weights_available": True,
            "report_period_date": "2025-09-30",
        }
        assert self._gate(payload) is True

    def test_usable_coverage_quality_passes(self):
        payload = {
            "fetch_status": "success",
            "holdings_count": 50,
            "weights_available": True,
            "report_period_date": "2025-12-31",
            "coverage_quality": "usable",
            "sector_status": "MISSING",
            "geography_status": "MISSING",
            "form_type": "NPORT-P",
        }
        assert self._gate(payload) is True

    def test_absent_coverage_quality_passes(self):
        """When coverage_quality is missing from payload, gate should pass (no suspicious/partial)."""
        payload = {
            "fetch_status": "success",
            "holdings_count": 50,
            "weights_available": True,
            "report_period_date": "2025-12-31",
        }
        assert self._gate(payload) is True

    def test_partial_coverage_quality_blocked(self):
        """coverage_quality containing 'partial' must NOT become holdings_ready."""
        for cq in ("partial", "partial_or_suspicious", "partial_coverage"):
            payload = {
                "fetch_status": "success",
                "holdings_count": 103,
                "weights_available": True,
                "report_period_date": "2025-12-31",
                "coverage_quality": cq,
            }
            assert self._gate(payload) is False, (
                f"Expected False for coverage_quality={cq!r} — partial must be blocked"
            )

    def test_suspicious_coverage_quality_blocked(self):
        """coverage_quality containing 'suspicious' must NOT become holdings_ready."""
        for cq in ("suspicious", "partial_or_suspicious", "looks_suspicious"):
            payload = {
                "fetch_status": "success",
                "holdings_count": 103,
                "weights_available": True,
                "report_period_date": "2025-12-31",
                "coverage_quality": cq,
            }
            assert self._gate(payload) is False, (
                f"Expected False for coverage_quality={cq!r} — suspicious must be blocked"
            )

    def test_usable_supplemental_coverage_quality_passes(self):
        """coverage_quality='usable_supplemental' contains neither 'partial' nor 'suspicious' — passes."""
        payload = {
            "fetch_status": "success",
            "holdings_count": 103,
            "weights_available": True,
            "report_period_date": "2025-12-31",
            "coverage_quality": "usable_supplemental",
        }
        assert self._gate(payload) is True


# ── Snapshot builder integration: holdings-ready NPORT → richer text ─────────


class TestNportHoldingsReadyWiresIntoSnapshot:
    """ETF with holdings-ready NPORT data in card_meta → composer uses holdings_ready tier."""

    _NPORT_HOLDINGS_READY = {
        "nport_output": {
            "fetch_status": "success",
            "holdings_count": 103,
            "weights_available": True,
            "report_period_date": "2025-12-31",
        }
    }

    def test_schd_holdings_ready_changes_context(self):
        """SCHD with NPORT holdings-ready → text says holdings data available."""
        card = _snap_card(
            "SCHD", "etf", action="HOLD", fit="UNKNOWN", eq="OK",
            extra_meta={"etf_provider_outputs": self._NPORT_HOLDINGS_READY},
        )
        ctx = card["detail_drawer_payload"].get("asset_intelligence_context")
        assert ctx is not None
        assert ctx["lens_applied"] == LENS_ETF_ROLE
        why = ctx["why_this_action"].lower()
        assert "full holdings data available" in why, (
            f"Expected 'full holdings data available' but got: {why!r}"
        )

    def test_vti_holdings_ready_changes_context(self):
        """VTI with NPORT holdings-ready → text says holdings data available."""
        card = _snap_card(
            "VTI", "etf", action="HOLD", fit="UNKNOWN", eq="OK",
            extra_meta={"etf_provider_outputs": self._NPORT_HOLDINGS_READY},
        )
        ctx = card["detail_drawer_payload"].get("asset_intelligence_context")
        assert ctx is not None
        assert ctx["lens_applied"] == LENS_ETF_ROLE
        why = ctx["why_this_action"].lower()
        assert "full holdings data available" in why

    def test_holdings_ready_no_longer_says_not_yet_wired(self):
        """Once holdings-ready, context must not say 'not yet wired'."""
        card = _snap_card(
            "VTI", "etf", action="HOLD", fit="UNKNOWN", eq="OK",
            extra_meta={"etf_provider_outputs": self._NPORT_HOLDINGS_READY},
        )
        ctx = card["detail_drawer_payload"].get("asset_intelligence_context")
        assert ctx is not None
        combined = " ".join([
            ctx.get("role_lens", ""),
            ctx.get("why_this_action", ""),
        ]).lower()
        assert "not yet wired" not in combined, (
            f"Holdings-ready ETF should not say 'not yet wired' but got: {combined!r}"
        )

    def test_context_safe_for_decision_always_false(self):
        """safe_for_decision must never be True even with holdings-ready data."""
        ctx = _etf_ctx(
            "VTI", fit=FIT_UNKNOWN, eq="OK", action="HOLD",
            provider_outputs=self._NPORT_HOLDINGS_READY,
        )
        assert ctx is not None
        # Context dict itself does not expose safe_for_decision — verify via
        # the composer result that safe_for_decision is False.
        from app.services.intelligence.v3.asset_intelligence_composer_v1 import compose_asset_intelligence
        result = compose_asset_intelligence(
            ticker="VTI",
            asset_type="etf",
            portfolio_fit=FIT_UNKNOWN,
            evidence_quality="OK",
            provider_outputs=self._NPORT_HOLDINGS_READY,
        )
        assert result.safe_for_decision is False

    def test_synthesis_ready_always_false(self):
        """synthesis_ready must never be True even with holdings-ready data."""
        from app.services.intelligence.v3.asset_intelligence_composer_v1 import compose_asset_intelligence
        result = compose_asset_intelligence(
            ticker="SCHD",
            asset_type="etf",
            portfolio_fit=FIT_UNKNOWN,
            evidence_quality="OK",
            provider_outputs={
                "nport_output": {
                    "fetch_status": "success",
                    "holdings_count": 103,
                    "weights_available": True,
                    "report_period_date": "2025-12-31",
                }
            },
        )
        assert result.synthesis_ready is False


# ── Honest degradation: no NPORT data → "not yet wired" preserved ────────────


class TestNoNportDataHonestDegradation:
    """ETF with no provider_outputs keeps the 'not yet wired' text."""

    def test_etf_no_provider_outputs_says_not_yet_wired(self):
        card = _snap_card("VTI", "etf", action="HOLD", fit="UNKNOWN", eq="OK")
        ctx = card["detail_drawer_payload"].get("asset_intelligence_context")
        assert ctx is not None
        assert ctx["lens_applied"] == LENS_ETF_ROLE
        why = ctx["why_this_action"].lower()
        assert "not yet wired" in why, (
            f"ETF with no provider data should say 'not yet wired' but got: {why!r}"
        )

    def test_etf_none_provider_outputs_says_not_yet_wired(self):
        card = _snap_card(
            "SCHD", "etf", action="HOLD", fit="UNKNOWN", eq="OK",
            extra_meta={"etf_provider_outputs": None},
        )
        ctx = card["detail_drawer_payload"].get("asset_intelligence_context")
        assert ctx is not None
        why = ctx["why_this_action"].lower()
        assert "not yet wired" in why

    def test_etf_empty_dict_provider_outputs_says_not_yet_wired(self):
        card = _snap_card(
            "VXUS", "etf", action="HOLD", fit="UNKNOWN", eq="OK",
            extra_meta={"etf_provider_outputs": {}},
        )
        ctx = card["detail_drawer_payload"].get("asset_intelligence_context")
        assert ctx is not None
        why = ctx["why_this_action"].lower()
        assert "not yet wired" in why


# ── coverage_quality partial/suspicious blocks holdings_ready ─────────────────


class TestCoverageQualityGate:
    """Artifacts with partial/suspicious coverage_quality must NOT become holdings_ready.

    These tests exercise the full snapshot path to confirm the safety gate works
    end-to-end, not just in the unit-level _gate helper in TestNportPayloadGate.
    """

    _PARTIAL_COVERAGE_NPORT = {
        "nport_output": {
            "fetch_status": "success",
            "holdings_count": 103,
            "weights_available": True,
            "report_period_date": "2025-12-31",
            "coverage_quality": "partial_or_suspicious",
        }
    }

    _USABLE_COVERAGE_NPORT = {
        "nport_output": {
            "fetch_status": "success",
            "holdings_count": 103,
            "weights_available": True,
            "report_period_date": "2025-12-31",
            "coverage_quality": "usable",
        }
    }

    def test_partial_coverage_does_not_say_full_holdings_available(self):
        """NPORT artifact with partial_or_suspicious coverage must NOT say 'full holdings data available'."""
        card = _snap_card(
            "VTI", "etf", action="HOLD", fit="UNKNOWN", eq="OK",
            extra_meta={"etf_provider_outputs": self._PARTIAL_COVERAGE_NPORT},
        )
        ctx = card["detail_drawer_payload"].get("asset_intelligence_context")
        assert ctx is not None
        why = ctx["why_this_action"].lower()
        assert "full holdings data available" not in why, (
            f"partial/suspicious coverage must not claim full holdings available; got: {why!r}"
        )

    def test_partial_coverage_keeps_honest_not_yet_wired(self):
        """NPORT with partial/suspicious coverage should degrade to 'not yet wired' text."""
        card = _snap_card(
            "VTI", "etf", action="HOLD", fit="UNKNOWN", eq="OK",
            extra_meta={"etf_provider_outputs": self._PARTIAL_COVERAGE_NPORT},
        )
        ctx = card["detail_drawer_payload"].get("asset_intelligence_context")
        assert ctx is not None
        why = ctx["why_this_action"].lower()
        assert "not yet wired" in why, (
            f"partial/suspicious NPORT should degrade to 'not yet wired' but got: {why!r}"
        )

    def test_partial_coverage_never_holdings_ready_tier(self):
        """Classifier tier for partial/suspicious NPORT must never be holdings_ready."""
        from app.services.intelligence.v3.etf_intelligence_classifier_v1 import classify_etf_intelligence
        cls = classify_etf_intelligence(
            ticker="VTI",
            asset_type="etf",
            provider_outputs=self._PARTIAL_COVERAGE_NPORT,
        )
        assert cls.evidence_tier != ETF_TIER_HOLDINGS_READY, (
            f"partial/suspicious NPORT must not produce holdings_ready tier; got {cls.evidence_tier!r}"
        )

    def test_partial_coverage_safe_for_decision_always_false(self):
        """safe_for_decision must never be True for partial/suspicious NPORT."""
        from app.services.intelligence.v3.etf_intelligence_classifier_v1 import classify_etf_intelligence
        cls = classify_etf_intelligence(
            ticker="VTI",
            asset_type="etf",
            provider_outputs=self._PARTIAL_COVERAGE_NPORT,
        )
        assert cls.safety_flags.get("safe_for_decision") is False

    def test_usable_coverage_quality_becomes_holdings_ready(self):
        """When coverage_quality='usable' and all other fields pass, ETF becomes holdings_ready."""
        from app.services.intelligence.v3.etf_intelligence_classifier_v1 import classify_etf_intelligence
        cls = classify_etf_intelligence(
            ticker="VTI",
            asset_type="etf",
            provider_outputs=self._USABLE_COVERAGE_NPORT,
        )
        assert cls.evidence_tier == ETF_TIER_HOLDINGS_READY


# ── AV missing-date stays profile-ready ───────────────────────────────────────


class TestAvMissingDateStaysProfileReady:
    """AV output without as_of_date stays supplemental/profile-ready, never holdings_ready."""

    _AV_NO_DATE = {
        "av_output": {
            "holdings_available": True,
            "as_of_date_verified": False,
            "coverage_quality": "usable_supplemental",
            "canonical_ready": False,
            "safe_for_decision": False,
        }
    }

    def test_av_no_date_etf_produces_profile_ready_tier(self):
        from app.services.intelligence.v3.etf_intelligence_classifier_v1 import classify_etf_intelligence
        cls = classify_etf_intelligence(
            ticker="VTI",
            asset_type="etf",
            provider_outputs=self._AV_NO_DATE,
        )
        assert cls.evidence_tier == ETF_TIER_PROFILE_READY, (
            f"AV without date should produce profile_ready, got {cls.evidence_tier!r}"
        )

    def test_av_no_date_does_not_say_full_holdings_available(self):
        ctx = _etf_ctx("VTI", provider_outputs=self._AV_NO_DATE)
        assert ctx is not None
        combined = " ".join([
            ctx.get("role_lens", ""),
            ctx.get("why_this_action", ""),
        ]).lower()
        assert "full holdings data available" not in combined

    def test_av_no_date_safe_for_decision_false(self):
        from app.services.intelligence.v3.asset_intelligence_composer_v1 import compose_asset_intelligence
        result = compose_asset_intelligence(
            ticker="VTI",
            asset_type="etf",
            provider_outputs=self._AV_NO_DATE,
        )
        assert result.safe_for_decision is False
        assert result.synthesis_ready is False


# ── FMP 402/paywalled stays not-ready ────────────────────────────────────────


class TestFmpPaywalledNotReady:
    """FMP 402/paywalled fixture produces no holdings readiness."""

    _FMP_402 = {
        "fmp_output": {
            "fetch_status": "paywalled",
            "holdings_count": 0,
            "weights_available": False,
        }
    }

    def test_fmp_402_produces_metadata_only_tier(self):
        from app.services.intelligence.v3.etf_intelligence_classifier_v1 import classify_etf_intelligence
        cls = classify_etf_intelligence(
            ticker="SCHD",
            asset_type="etf",
            provider_outputs=self._FMP_402,
        )
        # SCHD is in _KNOWN_ETF_MAP so profile_ready is granted from metadata;
        # FMP 402 contributes nothing.
        assert cls.evidence_tier in (ETF_TIER_PROFILE_READY, ETF_TIER_METADATA_ONLY), (
            f"FMP 402 should not contribute holdings readiness; got {cls.evidence_tier!r}"
        )

    def test_fmp_402_not_holdings_ready(self):
        from app.services.intelligence.v3.etf_intelligence_classifier_v1 import (
            classify_etf_intelligence,
            ETF_TIER_HOLDINGS_READY,
        )
        cls = classify_etf_intelligence(
            ticker="SCHD",
            asset_type="etf",
            provider_outputs=self._FMP_402,
        )
        assert cls.evidence_tier != ETF_TIER_HOLDINGS_READY

    def test_fmp_402_safe_for_decision_false(self):
        from app.services.intelligence.v3.asset_intelligence_composer_v1 import compose_asset_intelligence
        result = compose_asset_intelligence(
            ticker="SCHD",
            asset_type="etf",
            provider_outputs=self._FMP_402,
        )
        assert result.safe_for_decision is False
        assert result.synthesis_ready is False


# ── Regression gate: lens routing unchanged ───────────────────────────────────


class TestLensRegressionGate:
    """VTI/SCHD/VXUS retain ETF lens; GLD retains commodity hedge lens."""

    _NPORT_HOLDINGS_READY = {
        "nport_output": {
            "fetch_status": "success",
            "holdings_count": 200,
            "weights_available": True,
            "report_period_date": "2025-12-31",
        }
    }

    @pytest.mark.parametrize("ticker", ["VTI", "SCHD", "VXUS"])
    def test_etf_tickers_retain_etf_lens_without_provider_data(self, ticker):
        ctx = _etf_ctx(ticker)
        assert ctx is not None
        assert ctx["lens_applied"] == LENS_ETF_ROLE

    @pytest.mark.parametrize("ticker", ["VTI", "SCHD", "VXUS"])
    def test_etf_tickers_retain_etf_lens_with_nport_data(self, ticker):
        ctx = _etf_ctx(ticker, provider_outputs=self._NPORT_HOLDINGS_READY)
        assert ctx is not None
        assert ctx["lens_applied"] == LENS_ETF_ROLE

    def test_gld_retains_commodity_hedge_lens_without_provider_data(self):
        ctx = _etf_ctx("GLD")
        assert ctx is not None
        assert ctx["lens_applied"] == LENS_COMMODITY_HEDGE

    def test_gld_retains_commodity_hedge_lens_with_nport_data(self):
        # GLD is a commodity trust — NPORT provider data does not change the lens.
        ctx = _etf_ctx("GLD", provider_outputs=self._NPORT_HOLDINGS_READY)
        assert ctx is not None
        assert ctx["lens_applied"] == LENS_COMMODITY_HEDGE

    def test_gld_does_not_say_full_holdings_available(self):
        # Commodity trusts use not_applicable tier — holdings language must not appear.
        ctx = _etf_ctx("GLD", provider_outputs=self._NPORT_HOLDINGS_READY)
        assert ctx is not None
        combined = " ".join([
            ctx.get("role_lens", ""),
            ctx.get("why_this_action", ""),
        ]).lower()
        assert "full holdings data available" not in combined

    def test_gld_safe_for_decision_always_false(self):
        from app.services.intelligence.v3.asset_intelligence_composer_v1 import compose_asset_intelligence
        result = compose_asset_intelligence(
            ticker="GLD",
            asset_type="etf",
            provider_outputs=self._NPORT_HOLDINGS_READY,
        )
        assert result.safe_for_decision is False
        assert result.synthesis_ready is False


# ── Stocks and crypto unaffected ──────────────────────────────────────────────


class TestStockCryptoUnaffected:
    """Stock/crypto asset types are unaffected by ETF provider output wiring."""

    def test_stock_uses_fundamental_lens(self):
        ctx = _stock_ctx("MSFT", fit=FIT_UNKNOWN, eq="OK")
        assert ctx is not None
        assert ctx["lens_applied"] == LENS_STOCK_FUNDAMENTAL

    def test_crypto_uses_crypto_lens(self):
        ctx = _crypto_ctx("BTC")
        assert ctx is not None
        assert ctx["lens_applied"] == LENS_CRYPTO

    def test_stock_snapshot_unaffected_by_nport_map_absence(self):
        """Stock card_meta without etf_provider_outputs works normally."""
        card = _snap_card("MSFT", "Technology", action="HOLD", fit="UNKNOWN", eq="OK")
        ctx = card["detail_drawer_payload"].get("asset_intelligence_context")
        assert ctx is not None
        assert ctx["lens_applied"] == LENS_STOCK_FUNDAMENTAL

    def test_stock_safe_for_decision_false(self):
        from app.services.intelligence.v3.asset_intelligence_composer_v1 import compose_asset_intelligence
        result = compose_asset_intelligence(
            ticker="MSFT",
            asset_type="Technology",
            portfolio_fit=FIT_UNKNOWN,
            evidence_quality="OK",
        )
        assert result.safe_for_decision is False
        assert result.synthesis_ready is False


# ── Visible action preservation ───────────────────────────────────────────────


class TestVisibleActionPreserved:
    """The composer's suggested_action is diagnostic only; existing visible action is preserved."""

    @pytest.mark.parametrize("action", ["BUY", "HOLD", "TRIM", "SELL"])
    def test_action_preserved_in_snapshot_card(self, action):
        """Visible action in snapshot card matches the decision input, not composer output."""
        card = _snap_card(
            "VTI", "etf", action=action, fit="UNKNOWN", eq="OK",
            extra_meta={"etf_provider_outputs": {
                "nport_output": {
                    "fetch_status": "success",
                    "holdings_count": 200,
                    "weights_available": True,
                    "report_period_date": "2025-12-31",
                }
            }},
        )
        assert card["action"] == action, (
            f"Expected action={action!r} but got {card['action']!r}"
        )

    def test_nport_holdings_ready_does_not_override_hold_action(self):
        """BUY-language conflict filter prevents HOLD card from showing BUY-intent text."""
        ctx = _etf_ctx(
            "VTI", fit=FIT_UNDERWEIGHT, eq="OK", action="HOLD",
            provider_outputs={
                "nport_output": {
                    "fetch_status": "success",
                    "holdings_count": 200,
                    "weights_available": True,
                    "report_period_date": "2025-12-31",
                }
            },
        )
        assert ctx is not None
        why = ctx["why_this_action"].lower()
        # BUY-conflict phrases must be filtered for HOLD action
        assert "adding builds the intended portfolio sleeve" not in why


# ── Schema integrity ──────────────────────────────────────────────────────────


class TestSchemaIntegrity:
    """Context dict always has required keys regardless of provider data state."""

    _REQUIRED_KEYS = (
        "role_lens", "why_this_action", "add_more_trigger",
        "trim_sell_trigger", "lens_applied", "asset_class_display", "adapter_version",
    )

    def test_holdings_ready_context_has_all_keys(self):
        ctx = _etf_ctx(
            "VTI", provider_outputs={
                "nport_output": {
                    "fetch_status": "success",
                    "holdings_count": 200,
                    "weights_available": True,
                    "report_period_date": "2025-12-31",
                }
            }
        )
        assert ctx is not None
        for k in self._REQUIRED_KEYS:
            assert k in ctx, f"Key {k!r} missing from holdings-ready context"

    def test_no_provider_context_has_all_keys(self):
        ctx = _etf_ctx("SCHD")
        assert ctx is not None
        for k in self._REQUIRED_KEYS:
            assert k in ctx, f"Key {k!r} missing from no-provider context"

    def test_stock_context_has_all_keys(self):
        ctx = _stock_ctx("MSFT")
        assert ctx is not None
        for k in self._REQUIRED_KEYS:
            assert k in ctx, f"Key {k!r} missing from stock context"
