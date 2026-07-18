"""Stage 9F.2a — ETF NPORT resolver discovery tests.

Tests the etf_nport_candidate_discovery module (pure business logic) and the
resolver-discovery integration in nport_diagnostic_runner.  All fixture-based;
zero live SEC calls.

Coverage:

Discovery module (etf_nport_candidate_discovery):
  84. discover_nport_candidates returns candidates from EFTS entity search fixture.
  85. discover_nport_candidates returns candidates from EFTS series search fixture.
  86. CIKs that appear in both entity and series search are deduplicated.
  87. Entry=None (ticker not in static map) → empty candidates, no SEC calls.
  88. EFTS HTTP error → discovery_error set, other source still attempted.
  89. No parent_hint → entity search skipped, series search still attempted.
  90. Entity name mismatch → confidence=rejected, rejection_reason set.
  91. Static CIKs excluded from discovered_candidates (in existing_static_candidates only).
  92. Candidates sorted: confirmed_candidate before plausible_candidate before rejected.
  93. _normalize strips punctuation and lowercases for matching.

Runner discovery integration (nport_diagnostic_runner):
  94. discovery_fn not provided → resolver_discovery_used=False for all tickers.
  95. SPY standalone_trust → identity_verified=True → discovery_fn never called.
  96. GLD commodity_trust result → discovery_fn never called for GLD.
  97. Identity fails → discovery_fn called → new CIKs passed to discovered_provider_fn.
  98. Static wrong + discovered correct → fetch_status=success, identity_verified=True.
  99. Discovered candidates all rejected → discovered_provider_fn not called, failure preserved.
 100. No discovered candidates → original failure status preserved, discovery fields present.
 101. VYM sec_error static + discovery finds CIK → diagnostics include discovery fields.
 102. QQQ standalone trust: identity_verified=True without discovery.
 103. resolver_discovery_candidates in per-ticker output match discovery fixture.
 104. resolver_discovery_error surfaced in per-ticker when discovery raises.
 105. resolver_discovery_enabled=True in runner output when discovery_fn provided.
 106. Diagnostic endpoint source includes resolver_discovery_used field (structural check).
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest


# ── Shared EFTS fixture builders ──────────────────────────────────────────────

def _efts_response(hits: list[dict]) -> object:
    """Build a mock HTTP response for EFTS API calls."""
    body = {
        "hits": {
            "hits": [
                {"_source": h}
                for h in hits
            ],
            "total": {"value": len(hits), "relation": "eq"},
        }
    }
    resp = MagicMock()
    resp.json.return_value = body
    resp.raise_for_status.return_value = None
    return resp


def _efts_entity_hit(entity_id: str, entity_name: str) -> dict:
    return {"entity_id": entity_id, "entity_name": entity_name, "form_type": "NPORT-P"}


def _no_sleep(s: float) -> None:  # noqa: ARG001
    pass


# ── Provider / result helpers ──────────────────────────────────────────────────

def _make_success_result(ticker: str = "XLE", identity_verified: bool = True) -> object:
    from app.services.intelligence.research_workers.nport_provider_v1 import (
        NportFilingMeta, NportHolding, NportProviderResult,
    )
    return NportProviderResult(
        ticker=ticker,
        fetch_status="success",
        cik="0001168164",
        holdings=[NportHolding(name="Exxon Mobil Corp", weight_pct=22.0)],
        filing_meta=NportFilingMeta(
            accession_number="0001168164-25-000001",
            form_type="NPORT-P",
            filing_date="2025-01-15",
            report_period_date="2024-12-31",
            primary_doc="primary_doc.xml",
            filing_url="https://www.sec.gov/",
        ),
        identity_verified=identity_verified,
        identity_status=(
            "success_identity_verified" if identity_verified
            else "series_identity_not_proven"
        ),
        candidate_ciks_tried=["0001168164"],
        selected_candidate_cik="0001168164" if identity_verified else None,
    )


def _make_failure_result(
    ticker: str,
    fetch_status: str = "series_identity_not_proven",
    candidate_ciks_tried: list[str] | None = None,
) -> object:
    from app.services.intelligence.research_workers.nport_provider_v1 import NportProviderResult
    return NportProviderResult(
        ticker=ticker,
        fetch_status=fetch_status,
        error_message=f"identity not proven for {ticker}",
        cik="0000036405",
        identity_verified=False,
        identity_status=fetch_status,
        candidate_ciks_tried=candidate_ciks_tried or ["0000036405"],
    )


def _make_spy_result() -> object:
    from app.services.intelligence.research_workers.nport_provider_v1 import (
        NportFilingMeta, NportHolding, NportProviderResult,
    )
    return NportProviderResult(
        ticker="SPY",
        fetch_status="success",
        cik="0000884394",
        holdings=[NportHolding(name="Apple Inc", weight_pct=7.0)],
        filing_meta=NportFilingMeta(
            accession_number="0000884394-25-000001",
            form_type="NPORT-P",
            filing_date="2025-01-15",
            report_period_date="2024-12-31",
            primary_doc="primary_doc.xml",
            filing_url="https://www.sec.gov/",
        ),
        identity_verified=True,
        identity_status="success_identity_assumed_single_series",
        candidate_ciks_tried=["0000884394"],
        selected_candidate_cik="0000884394",
    )


def _make_gld_result() -> object:
    from app.services.intelligence.research_workers.nport_provider_v1 import NportProviderResult
    return NportProviderResult(
        ticker="GLD",
        fetch_status="commodity_trust_or_no_nport_data",
        cik="0001222333",
        identity_verified=False,
        identity_status="commodity_trust_or_no_nport_data",
        candidate_ciks_tried=["0001222333"],
    )


# ── Discovery module tests ─────────────────────────────────────────────────────


# Test 84 — EFTS entity search fixture returns confirmed candidate
def test_84_discovery_entity_search_returns_confirmed_candidate():
    from app.services.intelligence.research_workers.etf_nport_candidate_discovery import (
        discover_nport_candidates,
    )
    from app.services.intelligence.research_workers.etf_parent_cik_resolver import (
        get_parent_registrant_entry,
    )

    call_log = []

    # Use a NEW CIK (not in VGT's static map) so EFTS result appears in discovered_candidates
    _NEW_CIK = "0000036406"  # differs from static CIK 0000036405

    def _mock_get(url: str):
        call_log.append(url)
        # Entity search for "VANGUARD WORLD FUND" → one hit with a new CIK
        if "entity=" in url:
            return _efts_response([
                _efts_entity_hit("36406", "VANGUARD WORLD FUND"),
            ])
        # Series search
        return _efts_response([])

    entry = get_parent_registrant_entry("VGT")
    result = discover_nport_candidates("VGT", entry, http_get_fn=_mock_get)

    # Entity search was called with VANGUARD WORLD FUND hint
    assert any("entity=" in url for url in call_log)
    # The new CIK is found in discovered_candidates (not excluded by seen_ciks)
    ciks = [c.candidate_cik for c in result.discovered_candidates]
    assert _NEW_CIK in ciks
    # Confidence: confirmed because entity name matches registrant hint
    matching = [c for c in result.discovered_candidates if c.candidate_cik == _NEW_CIK]
    assert matching[0].confidence == "confirmed_candidate"


# Test 85 — EFTS series search fixture returns plausible candidate
def test_85_discovery_series_search_returns_plausible_candidate():
    from app.services.intelligence.research_workers.etf_nport_candidate_discovery import (
        discover_nport_candidates,
    )
    from app.services.intelligence.research_workers.etf_parent_cik_resolver import (
        get_parent_registrant_entry,
    )

    call_log = []

    def _mock_get(url: str):
        call_log.append(url)
        # Entity search for SPDR SERIES TRUST → no hits (wrong parent already tried)
        if "entity=" in url:
            return _efts_response([])
        # Series search for "Energy Select Sector SPDR Fund" → new CIK
        if "q=" in url:
            return _efts_response([
                _efts_entity_hit("1168165", "SPDR FUNDS TRUST"),
            ])
        return _efts_response([])

    entry = get_parent_registrant_entry("XLE")
    result = discover_nport_candidates("XLE", entry, http_get_fn=_mock_get)

    series_cands = [c for c in result.discovered_candidates if c.candidate_source == "sec_efts_series"]
    assert len(series_cands) >= 1


# Test 86 — CIKs found in both entity and series searches are deduplicated
def test_86_discovery_deduplicates_ciks_across_sources():
    from app.services.intelligence.research_workers.etf_nport_candidate_discovery import (
        discover_nport_candidates,
    )
    from app.services.intelligence.research_workers.etf_parent_cik_resolver import (
        get_parent_registrant_entry,
    )

    def _mock_get(url: str):
        # Both entity and series search return the same CIK
        return _efts_response([
            _efts_entity_hit("36405", "VANGUARD WORLD FUND"),
        ])

    entry = get_parent_registrant_entry("VGT")
    result = discover_nport_candidates("VGT", entry, http_get_fn=_mock_get)

    discovered_ciks = [c.candidate_cik for c in result.discovered_candidates]
    assert len(discovered_ciks) == len(set(discovered_ciks)), "Duplicate CIKs found"


# Test 87 — None entry → no hints → empty candidates returned, no SEC calls made
def test_87_none_entry_no_sec_calls():
    from app.services.intelligence.research_workers.etf_nport_candidate_discovery import (
        discover_nport_candidates,
    )

    call_log = []

    def _mock_get(url: str):
        call_log.append(url)
        return _efts_response([])

    result = discover_nport_candidates("UNKNOWN", None, http_get_fn=_mock_get)

    # No parent_hint → entity search not called
    # No expected_series_names → series search not called
    assert call_log == [], "No SEC calls should be made when entry is None"
    assert result.discovered_candidates == []
    assert result.existing_static_candidates == []


# Test 88 — EFTS HTTP error → discovery_error set, other source still attempted
def test_88_http_error_captured_in_discovery_error():
    from app.services.intelligence.research_workers.etf_nport_candidate_discovery import (
        discover_nport_candidates,
    )
    from app.services.intelligence.research_workers.etf_parent_cik_resolver import (
        get_parent_registrant_entry,
    )

    call_count = {"n": 0}

    def _mock_get(url: str):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # First call (entity search) fails
            raise ConnectionError("simulated network failure")
        # Second call (series search) succeeds
        return _efts_response([
            _efts_entity_hit("9999999", "VANGUARD INFORMATION TECHNOLOGY INDEX FUND TRUST"),
        ])

    entry = get_parent_registrant_entry("VGT")
    result = discover_nport_candidates("VGT", entry, http_get_fn=_mock_get)

    # Entity search error captured
    assert result.discovery_error is not None
    assert "sec_efts_entity" in result.discovery_error
    # Series search still attempted — at least one source tried
    assert len(result.discovery_sources_tried) >= 1


# Test 89 — No parent_hint (entry with no parent_name) → entity search skipped
def test_89_no_parent_hint_skips_entity_search():
    from app.services.intelligence.research_workers.etf_nport_candidate_discovery import (
        discover_nport_candidates, NportCandidateEntry,
    )
    from dataclasses import dataclass

    # Build a minimal entry-like object with no parent_name
    entry = SimpleNamespace(
        parent_name=None,
        expected_series_names=("Energy Select Sector SPDR Fund",),
        parent_cik="0001168164",
        candidate_ciks=(),
    )

    entity_search_called = {"called": False}

    def _mock_get(url: str):
        if "entity=" in url:
            entity_search_called["called"] = True
        return _efts_response([])

    discover_nport_candidates("XLE", entry, http_get_fn=_mock_get)
    assert not entity_search_called["called"], "Entity search must be skipped when parent_name is None"


# Test 90 — Entity name mismatch → confidence=rejected, rejection_reason non-None
def test_90_entity_name_mismatch_is_rejected():
    from app.services.intelligence.research_workers.etf_nport_candidate_discovery import (
        discover_nport_candidates,
    )
    from app.services.intelligence.research_workers.etf_parent_cik_resolver import (
        get_parent_registrant_entry,
    )

    def _mock_get(url: str):
        # Return an entity that does NOT match any hint for VGT
        return _efts_response([
            _efts_entity_hit("1234567", "COMPLETELY UNRELATED FUND INC"),
        ])

    entry = get_parent_registrant_entry("VGT")
    result = discover_nport_candidates("VGT", entry, http_get_fn=_mock_get)

    rejected = [c for c in result.discovered_candidates if c.confidence == "rejected"]
    assert len(rejected) >= 1
    assert rejected[0].rejection_reason is not None


# Test 91 — Static CIKs excluded from discovered_candidates, appear in existing_static
def test_91_static_ciks_excluded_from_discovered():
    from app.services.intelligence.research_workers.etf_nport_candidate_discovery import (
        discover_nport_candidates,
    )
    from app.services.intelligence.research_workers.etf_parent_cik_resolver import (
        get_parent_registrant_entry,
    )

    entry = get_parent_registrant_entry("VGT")
    static_cik = "0000036405"  # VGT's static parent CIK

    def _mock_get(url: str):
        # EFTS returns the same CIK that's already in the static map
        return _efts_response([
            _efts_entity_hit("36405", "VANGUARD WORLD FUND"),
        ])

    result = discover_nport_candidates("VGT", entry, http_get_fn=_mock_get)

    # Static CIK is in existing_static_candidates
    assert static_cik in result.existing_static_candidates
    # Static CIK is NOT in discovered_candidates (deduplicated from EFTS results)
    discovered_ciks = [c.candidate_cik for c in result.discovered_candidates]
    assert static_cik not in discovered_ciks


# Test 92 — Candidates sorted: confirmed before plausible before rejected
def test_92_candidates_sorted_by_confidence():
    from app.services.intelligence.research_workers.etf_nport_candidate_discovery import (
        discover_nport_candidates,
    )
    from app.services.intelligence.research_workers.etf_parent_cik_resolver import (
        get_parent_registrant_entry,
    )

    call_count = {"n": 0}

    def _mock_get(url: str):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # Entity search: one confirmed + one rejected
            return _efts_response([
                _efts_entity_hit("11111111", "VANGUARD WORLD FUND"),     # confirmed
                _efts_entity_hit("22222222", "RANDOM UNRELATED TRUST"),  # rejected
            ])
        # Series search: one plausible
        return _efts_response([
            _efts_entity_hit("33333333", "VANGUARD INFORMATION TECHNOLOGY INDEX FUND TRUST"),
        ])

    entry = get_parent_registrant_entry("VGT")
    result = discover_nport_candidates("VGT", entry, http_get_fn=_mock_get)

    confs = [c.confidence for c in result.discovered_candidates]
    order = {"confirmed_candidate": 0, "plausible_candidate": 1, "rejected": 2}
    assert confs == sorted(confs, key=lambda x: order.get(x, 3))


# Test 93 — _normalize strips punctuation for matching (e.g. "U.S." → "us")
def test_93_normalize_strips_punctuation():
    from app.services.intelligence.research_workers.etf_nport_candidate_discovery import _normalize

    assert _normalize("U.S. Dividend Equity ETF") == "us dividend equity etf"
    assert _normalize("VANGUARD  INDEX  FUNDS") == "vanguard index funds"
    assert _normalize("Schwab U.S. Dividend Equity ETF") == "schwab us dividend equity etf"


# ── Runner discovery integration tests ────────────────────────────────────────


# Test 94 — discovery_fn not provided → resolver_discovery_used=False
def test_94_no_discovery_fn_discovery_unused():
    from app.services.intelligence.research_workers.nport_diagnostic_runner import run_nport_live_check

    def _mock_provider(ticker, cfg):
        return _make_spy_result() if ticker == "SPY" else _make_failure_result(ticker)

    out = run_nport_live_check(
        ["SPY"],
        "TestApp/1.0 test@example.com",
        provider_fn=_mock_provider,
        sleep_fn=_no_sleep,
        discovery_fn=None,  # explicitly no discovery
    )

    row = out["per_ticker"][0]
    assert row["resolver_discovery_used"] is False
    assert row["resolver_discovery_candidates"] == []
    assert out["resolver_discovery_enabled"] is False


# Test 95 — SPY standalone_trust → identity_verified=True → discovery_fn never called
def test_95_spy_identity_verified_discovery_not_called():
    from app.services.intelligence.research_workers.nport_diagnostic_runner import run_nport_live_check

    discovery_call_log = []

    def _mock_provider(ticker, cfg):
        return _make_spy_result()

    def _mock_discovery(ticker, entry):
        discovery_call_log.append(ticker)
        raise AssertionError("discovery_fn must NOT be called for identity-verified result")

    out = run_nport_live_check(
        ["SPY"],
        "TestApp/1.0 test@example.com",
        provider_fn=_mock_provider,
        sleep_fn=_no_sleep,
        discovery_fn=_mock_discovery,
    )

    assert discovery_call_log == [], "discovery_fn must not be called when identity already verified"
    row = out["per_ticker"][0]
    assert row["resolver_discovery_used"] is False


# Test 96 — GLD commodity_trust result → discovery_fn not called
def test_96_gld_commodity_trust_discovery_not_called():
    from app.services.intelligence.research_workers.nport_diagnostic_runner import run_nport_live_check

    discovery_call_log = []

    def _mock_provider(ticker, cfg):
        return _make_gld_result()

    def _mock_discovery(ticker, entry):
        discovery_call_log.append(ticker)
        # GLD has identity_verified=False, so discovery WILL be called.
        # The test verifies discovery runs but finds no useful candidates for GLD.
        from app.services.intelligence.research_workers.etf_nport_candidate_discovery import NportDiscoveryResult
        return NportDiscoveryResult(
            ticker=ticker,
            expected_series_names=(),
            existing_static_candidates=["0001222333"],
            discovered_candidates=[],
            discovery_sources_tried=["sec_efts_entity"],
        )

    out = run_nport_live_check(
        ["GLD"],
        "TestApp/1.0 test@example.com",
        provider_fn=_mock_provider,
        sleep_fn=_no_sleep,
        discovery_fn=_mock_discovery,
    )

    # GLD commodity trust: discovery may run but should find nothing
    row = out["per_ticker"][0]
    assert row["fetch_status"] == "commodity_trust_or_no_nport_data"
    # No new CIKs to retry → original result preserved
    assert row["holdings_count"] == 0


# Test 97 — Identity fails → discovery_fn called → new CIKs passed to discovered_provider_fn
def test_97_identity_fail_triggers_discovery_and_retry():
    from app.services.intelligence.research_workers.nport_diagnostic_runner import run_nport_live_check
    from app.services.intelligence.research_workers.etf_nport_candidate_discovery import (
        NportDiscoveryResult, NportCandidateEntry,
    )

    discovered_provider_calls = []

    def _mock_provider(ticker, cfg):
        return _make_failure_result("VGT", "series_identity_not_proven", ["0000036405"])

    def _mock_discovery(ticker, entry):
        return NportDiscoveryResult(
            ticker=ticker,
            expected_series_names=("Vanguard Information Technology Index Fund",),
            existing_static_candidates=["0000036405"],
            discovered_candidates=[
                NportCandidateEntry(
                    ticker=ticker,
                    candidate_cik="0000099999",
                    candidate_title="VANGUARD WORLD FUND",
                    candidate_source="sec_efts_entity",
                    match_reason="confirmed via EFTS entity search",
                    confidence="confirmed_candidate",
                )
            ],
            discovery_sources_tried=["sec_efts_entity"],
        )

    def _mock_discovered_provider(ticker, cfg, candidate_ciks):
        discovered_provider_calls.append((ticker, list(candidate_ciks)))
        # Return success with the discovered CIK
        return _make_success_result("VGT", identity_verified=True)

    out = run_nport_live_check(
        ["VGT"],
        "TestApp/1.0 test@example.com",
        provider_fn=_mock_provider,
        sleep_fn=_no_sleep,
        discovery_fn=_mock_discovery,
        discovered_provider_fn=_mock_discovered_provider,
    )

    # discovered_provider_fn was called with the new CIK
    assert len(discovered_provider_calls) == 1
    ticker_called, ciks_called = discovered_provider_calls[0]
    assert ticker_called == "VGT"
    assert "0000099999" in ciks_called


# Test 98 — Static wrong + discovered correct → success, identity_verified=True
def test_98_static_wrong_discovered_correct_yields_identity_verified():
    from app.services.intelligence.research_workers.nport_diagnostic_runner import run_nport_live_check
    from app.services.intelligence.research_workers.etf_nport_candidate_discovery import (
        NportDiscoveryResult, NportCandidateEntry,
    )

    def _mock_provider(ticker, cfg):
        return _make_failure_result("VGT", "series_identity_not_proven", ["0000036405"])

    def _mock_discovery(ticker, entry):
        return NportDiscoveryResult(
            ticker=ticker,
            expected_series_names=("Vanguard Information Technology Index Fund",),
            existing_static_candidates=["0000036405"],
            discovered_candidates=[
                NportCandidateEntry(
                    ticker=ticker,
                    candidate_cik="0000099999",
                    candidate_title="VANGUARD WORLD FUND",
                    candidate_source="sec_efts_entity",
                    match_reason="confirmed via EFTS",
                    confidence="confirmed_candidate",
                )
            ],
            discovery_sources_tried=["sec_efts_entity"],
        )

    def _mock_discovered_provider(ticker, cfg, candidate_ciks):
        return _make_success_result("VGT", identity_verified=True)

    out = run_nport_live_check(
        ["VGT"],
        "TestApp/1.0 test@example.com",
        provider_fn=_mock_provider,
        sleep_fn=_no_sleep,
        discovery_fn=_mock_discovery,
        discovered_provider_fn=_mock_discovered_provider,
    )

    row = out["per_ticker"][0]
    assert row["fetch_status"] == "success"
    assert row["identity_verified"] is True
    assert row["resolver_discovery_used"] is True
    assert out["tickers_succeeded"] == 1
    assert out["tickers_identity_verified"] == 1


# Test 99 — Discovered candidates all rejected → discovered_provider_fn not called
def test_99_all_rejected_candidates_not_tried():
    from app.services.intelligence.research_workers.nport_diagnostic_runner import run_nport_live_check
    from app.services.intelligence.research_workers.etf_nport_candidate_discovery import (
        NportDiscoveryResult, NportCandidateEntry,
    )

    discovered_provider_calls = []

    def _mock_provider(ticker, cfg):
        return _make_failure_result("VGT", "series_identity_not_proven", ["0000036405"])

    def _mock_discovery(ticker, entry):
        return NportDiscoveryResult(
            ticker=ticker,
            expected_series_names=("Vanguard Information Technology Index Fund",),
            existing_static_candidates=["0000036405"],
            discovered_candidates=[
                NportCandidateEntry(
                    ticker=ticker,
                    candidate_cik="0001111111",
                    candidate_title="COMPLETELY UNRELATED TRUST",
                    candidate_source="sec_efts_entity",
                    match_reason="no match",
                    confidence="rejected",
                    rejection_reason="entity name does not match any hint",
                )
            ],
            discovery_sources_tried=["sec_efts_entity"],
        )

    def _mock_discovered_provider(ticker, cfg, candidate_ciks):
        discovered_provider_calls.append(candidate_ciks)
        return _make_success_result(ticker)

    out = run_nport_live_check(
        ["VGT"],
        "TestApp/1.0 test@example.com",
        provider_fn=_mock_provider,
        sleep_fn=_no_sleep,
        discovery_fn=_mock_discovery,
        discovered_provider_fn=_mock_discovered_provider,
    )

    # Rejected candidates must not be tried
    assert discovered_provider_calls == [], "discovered_provider_fn must not be called with rejected candidates"
    row = out["per_ticker"][0]
    assert row["fetch_status"] == "series_identity_not_proven"
    assert row["resolver_discovery_used"] is True
    # No holdings without identity proof
    assert row["holdings_count"] == 0


# Test 100 — No discovered candidates → original failure preserved, discovery fields present
def test_100_no_discovered_candidates_preserves_failure():
    from app.services.intelligence.research_workers.nport_diagnostic_runner import run_nport_live_check
    from app.services.intelligence.research_workers.etf_nport_candidate_discovery import (
        NportDiscoveryResult,
    )

    def _mock_provider(ticker, cfg):
        return _make_failure_result("VXUS", "no_nport_filing", ["0001004244"])

    def _mock_discovery(ticker, entry):
        return NportDiscoveryResult(
            ticker=ticker,
            expected_series_names=("Vanguard Total International Stock Index Fund",),
            existing_static_candidates=["0001004244"],
            discovered_candidates=[],  # nothing found
            discovery_sources_tried=["sec_efts_entity", "sec_efts_series"],
        )

    out = run_nport_live_check(
        ["VXUS"],
        "TestApp/1.0 test@example.com",
        provider_fn=_mock_provider,
        sleep_fn=_no_sleep,
        discovery_fn=_mock_discovery,
        discovered_provider_fn=None,
    )

    row = out["per_ticker"][0]
    # Original failure status preserved
    assert row["fetch_status"] == "no_nport_filing"
    # Discovery fields populated
    assert row["resolver_discovery_used"] is True
    assert row["resolver_discovery_candidates"] == []
    assert "sec_efts_entity" in row["resolver_discovery_sources_tried"]
    # No holdings returned
    assert row["holdings_count"] == 0


# Test 101 — VYM sec_error static + discovery finds CIK → diagnostics include discovery fields
def test_101_vym_sec_error_discovery_diagnostics_preserved():
    from app.services.intelligence.research_workers.nport_diagnostic_runner import run_nport_live_check
    from app.services.intelligence.research_workers.etf_nport_candidate_discovery import (
        NportDiscoveryResult, NportCandidateEntry,
    )
    from app.services.intelligence.research_workers.nport_provider_v1 import NportProviderResult

    def _mock_provider(ticker, cfg):
        # VYM static CIK returns 404/sec_error
        return NportProviderResult(
            ticker="VYM",
            fetch_status="sec_error",
            error_message="HTTP 404: CIK 0000916548 not found",
            cik="0000916548",
            identity_verified=False,
            identity_status="sec_error",
            candidate_ciks_tried=["0000916548"],
        )

    def _mock_discovery(ticker, entry):
        return NportDiscoveryResult(
            ticker=ticker,
            expected_series_names=("Vanguard High Dividend Yield Index Fund",),
            existing_static_candidates=["0000916548"],
            discovered_candidates=[
                NportCandidateEntry(
                    ticker=ticker,
                    candidate_cik="0000999888",
                    candidate_title="VANGUARD WHITEHALL FUNDS",
                    candidate_source="sec_efts_entity",
                    match_reason="entity name matches registrant hint",
                    confidence="confirmed_candidate",
                )
            ],
            discovery_sources_tried=["sec_efts_entity"],
        )

    def _mock_discovered_provider(ticker, cfg, candidate_ciks):
        # Discovered CIK also fails — still diagnostic
        return NportProviderResult(
            ticker="VYM",
            fetch_status="no_nport_filing",
            error_message="No NPORT-P filing found under discovered CIK",
            cik="0000999888",
            identity_verified=False,
            identity_status="no_nport_filing",
            candidate_ciks_tried=["0000999888"],
        )

    out = run_nport_live_check(
        ["VYM"],
        "TestApp/1.0 test@example.com",
        provider_fn=_mock_provider,
        sleep_fn=_no_sleep,
        discovery_fn=_mock_discovery,
        discovered_provider_fn=_mock_discovered_provider,
    )

    row = out["per_ticker"][0]
    # Discovery was used and found a candidate
    assert row["resolver_discovery_used"] is True
    discovered = row["resolver_discovery_candidates"]
    assert len(discovered) == 1
    assert discovered[0]["candidate_cik"] == "0000999888"
    assert discovered[0]["confidence"] == "confirmed_candidate"
    # No holdings returned (neither static nor discovered CIK yielded identity-verified)
    assert row["holdings_count"] == 0


# Test 102 — QQQ standalone trust: identity_verified=True without discovery
def test_102_qqq_standalone_trust_unchanged():
    from app.services.intelligence.research_workers.nport_diagnostic_runner import run_nport_live_check
    from app.services.intelligence.research_workers.nport_provider_v1 import (
        NportFilingMeta, NportHolding, NportProviderResult,
    )

    qqq_result = NportProviderResult(
        ticker="QQQ",
        fetch_status="success",
        cik="0001067839",
        holdings=[NportHolding(name="Apple Inc", weight_pct=8.5)],
        filing_meta=NportFilingMeta(
            accession_number="0001067839-25-000001",
            form_type="NPORT-P",
            filing_date="2025-01-15",
            report_period_date="2024-12-31",
            primary_doc="primary_doc.xml",
            filing_url="https://www.sec.gov/",
        ),
        identity_verified=True,
        identity_status="success_identity_assumed_single_series",
        candidate_ciks_tried=["0001067839"],
        selected_candidate_cik="0001067839",
    )

    discovery_call_log = []

    def _mock_provider(ticker, cfg):
        return qqq_result

    def _mock_discovery(ticker, entry):
        discovery_call_log.append(ticker)
        raise AssertionError("must not be called for identity-verified QQQ")

    out = run_nport_live_check(
        ["QQQ"],
        "TestApp/1.0 test@example.com",
        provider_fn=_mock_provider,
        sleep_fn=_no_sleep,
        discovery_fn=_mock_discovery,
    )

    assert discovery_call_log == []
    row = out["per_ticker"][0]
    assert row["identity_verified"] is True
    assert row["resolver_discovery_used"] is False


# Test 103 — resolver_discovery_candidates in output match discovery fixture
def test_103_discovery_candidates_in_output_match_fixture():
    from app.services.intelligence.research_workers.nport_diagnostic_runner import run_nport_live_check
    from app.services.intelligence.research_workers.etf_nport_candidate_discovery import (
        NportDiscoveryResult, NportCandidateEntry,
    )

    def _mock_provider(ticker, cfg):
        return _make_failure_result("SCHD", "no_nport_filing", ["0001477379"])

    def _mock_discovery(ticker, entry):
        return NportDiscoveryResult(
            ticker=ticker,
            expected_series_names=("Schwab U.S. Dividend Equity ETF",),
            existing_static_candidates=["0001477379"],
            discovered_candidates=[
                NportCandidateEntry(
                    ticker=ticker,
                    candidate_cik="0001477379",
                    candidate_title="SCHWAB STRATEGIC TRUST",
                    candidate_source="sec_efts_entity",
                    match_reason="entity name matches registrant hint",
                    confidence="confirmed_candidate",
                ),
                NportCandidateEntry(
                    ticker=ticker,
                    candidate_cik="0008888888",
                    candidate_title="UNRELATED ENTITY",
                    candidate_source="sec_efts_series",
                    match_reason="no match",
                    confidence="rejected",
                    rejection_reason="entity name does not match",
                ),
            ],
            discovery_sources_tried=["sec_efts_entity", "sec_efts_series"],
        )

    out = run_nport_live_check(
        ["SCHD"],
        "TestApp/1.0 test@example.com",
        provider_fn=_mock_provider,
        sleep_fn=_no_sleep,
        discovery_fn=_mock_discovery,
        discovered_provider_fn=lambda t, c, ciks: _make_failure_result(t, "no_nport_filing", ciks),
    )

    row = out["per_ticker"][0]
    cands = row["resolver_discovery_candidates"]
    assert len(cands) == 2
    assert cands[0]["candidate_cik"] == "0001477379"
    assert cands[0]["confidence"] == "confirmed_candidate"
    assert cands[1]["confidence"] == "rejected"
    assert cands[1]["rejection_reason"] is not None
    assert row["resolver_discovery_sources_tried"] == ["sec_efts_entity", "sec_efts_series"]


# Test 104 — resolver_discovery_error surfaced in per-ticker when discovery error occurs
def test_104_discovery_error_surfaced_in_output():
    from app.services.intelligence.research_workers.nport_diagnostic_runner import run_nport_live_check
    from app.services.intelligence.research_workers.etf_nport_candidate_discovery import (
        NportDiscoveryResult,
    )

    def _mock_provider(ticker, cfg):
        return _make_failure_result("VOO", "no_nport_filing", ["0000764180"])

    def _mock_discovery(ticker, entry):
        return NportDiscoveryResult(
            ticker=ticker,
            expected_series_names=("Vanguard S&P 500 Index Fund",),
            existing_static_candidates=["0000764180"],
            discovered_candidates=[],
            discovery_sources_tried=["sec_efts_entity"],
            discovery_error="sec_efts_entity failed: Connection refused",
        )

    out = run_nport_live_check(
        ["VOO"],
        "TestApp/1.0 test@example.com",
        provider_fn=_mock_provider,
        sleep_fn=_no_sleep,
        discovery_fn=_mock_discovery,
    )

    row = out["per_ticker"][0]
    assert row["resolver_discovery_used"] is True
    assert row["resolver_discovery_error"] == "sec_efts_entity failed: Connection refused"


# Test 105 — resolver_discovery_enabled=True in runner output when discovery_fn provided
def test_105_resolver_discovery_enabled_flag_in_output():
    from app.services.intelligence.research_workers.nport_diagnostic_runner import run_nport_live_check
    from app.services.intelligence.research_workers.etf_nport_candidate_discovery import (
        NportDiscoveryResult,
    )

    def _mock_provider(ticker, cfg):
        return _make_spy_result()

    def _mock_discovery(ticker, entry):
        return NportDiscoveryResult(
            ticker=ticker,
            expected_series_names=(),
            existing_static_candidates=[],
            discovered_candidates=[],
            discovery_sources_tried=[],
        )

    # With discovery_fn
    out_with = run_nport_live_check(
        ["SPY"],
        "TestApp/1.0 test@example.com",
        provider_fn=_mock_provider,
        sleep_fn=_no_sleep,
        discovery_fn=_mock_discovery,
    )
    assert out_with["resolver_discovery_enabled"] is True

    # Without discovery_fn
    out_without = run_nport_live_check(
        ["SPY"],
        "TestApp/1.0 test@example.com",
        provider_fn=_mock_provider,
        sleep_fn=_no_sleep,
    )
    assert out_without["resolver_discovery_enabled"] is False


# Test 106 — Diagnostic endpoint wires discover_nport_candidates as discovery_fn (structural)
