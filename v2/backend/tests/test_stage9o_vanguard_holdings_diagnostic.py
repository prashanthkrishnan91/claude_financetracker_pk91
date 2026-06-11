"""Stage 9O — Vanguard Issuer-Official Holdings Diagnostic v1 tests.

Fixture-based only — no live HTTP calls, no SQL, no artifact writes.

Tests prove:
  O1–O3:  VTI, VOO, VXUS each return canonical_candidate when all criteria met.
  O4:     Missing as_of_date → supplemental_only with as_of_date_missing disqualifier.
  O5:     Missing weights → supplemental_only with weights_missing disqualifier.
  O6:     Identity mismatch → manual_research_required with identity_not_proven.
  O7:     Access failure (HTTP error) → rejected with access_failure disqualifier.
  O8:     safe_for_decision=False on every result, every path.
  O9:     canonical_ready=False on every result, every path.
  O10:    Multiple tickers with mixed outcomes evaluated independently in one run.
  O11:    diagnostic_version field is present and equals "stage9o_v1".
  O12:    All required evidence fields present in every per-ticker entry.
  O13:    Rejected result includes failure_reason; classification=rejected.
  O14:    Supplemental result includes non-empty disqualifiers list.
  O15:    as_of_date is None when date absent — not inferred or fabricated.
  O16:    holdings_weights_available is False when weights absent — not fabricated.
  O17:    identity_basis populated on success path.
  O18:    holdings_count is int >= 0 across all paths.
  O19:    url_used field is populated for all tickers in all paths.
  O20:    Each ticker evaluated independently (batch == sum of singles).
  O21:    Summary counts are consistent with per_ticker classifications.
  O22:    diagnostics_only=True and artifact_writes=0 on every result.
  O23:    policy_unchanged=True and visible_snapshot_unchanged=True always.
  O24:    Empty-body response → rejected.
  O25:    CSV with unrecognised column layout → rejected.
"""
from __future__ import annotations

import pytest


# ── CSV fixtures ───────────────────────────────────────────────────────────────

def _make_vanguard_csv(
    fund_name: str,
    as_of_line: str | None,
    include_weight_col: bool,
    rows: list[tuple[str, str]] | None = None,
) -> str:
    """Build a minimal Vanguard-style CSV for testing.

    fund_name:          Name in the first metadata row (fund identity).
    as_of_line:         "As of MM/DD/YYYY" row, or None to omit.
    include_weight_col: Whether to include "% of Fund" column.
    rows:               [(ticker, weight_str)] data rows. Uses defaults if None.
    """
    lines = [fund_name]
    if as_of_line:
        lines.append(as_of_line)
    lines.append("")  # blank separator

    if include_weight_col:
        lines.append("Ticker,Holdings,% of Fund")
    else:
        lines.append("Ticker,Holdings,Market Value")

    data = rows or [
        ("AAPL", "6.51"),
        ("MSFT", "5.98"),
        ("AMZN", "3.47"),
        ("NVDA", "3.12"),
        ("GOOG", "2.05"),
    ]
    for ticker_sym, weight_or_mv in data:
        lines.append(f"{ticker_sym},Holding Name {ticker_sym},{weight_or_mv}")

    return "\n".join(lines)


_FUND_NAMES = {
    "VTI":  "Vanguard Total Stock Market ETF",
    "VOO":  "Vanguard S&P 500 ETF",
    "VXUS": "Vanguard Total International Stock ETF",
}

_AS_OF = "As of 01/31/2024"


def _success_csv(ticker: str) -> str:
    return _make_vanguard_csv(_FUND_NAMES[ticker], _AS_OF, True)


def _no_date_csv(ticker: str) -> str:
    return _make_vanguard_csv(_FUND_NAMES[ticker], None, True)


def _no_weight_csv(ticker: str) -> str:
    return _make_vanguard_csv(_FUND_NAMES[ticker], _AS_OF, False)


def _identity_mismatch_csv(ticker: str) -> str:
    # Deliberately wrong fund, but must not contain "name"/"holding"/"ticker"/"symbol"
    # as substrings — those keywords trigger the header-row detector in the CSV parser.
    return _make_vanguard_csv("XYZ Global Asset Group Ltd", _AS_OF, True)


# ── Mock HTTP client ───────────────────────────────────────────────────────────

class _MockResponse:
    def __init__(self, text: str, raise_on_status: bool = False):
        self.text = text
        self._raise = raise_on_status

    def raise_for_status(self) -> None:
        if self._raise:
            raise Exception("HTTP 403 Forbidden")


def _make_http_fn(ticker_to_csv: dict[str, str]) -> object:
    """Return an injectable http_get_fn serving CSV per-ticker from the URL."""
    def fn(url: str) -> _MockResponse:
        for ticker, csv_text in ticker_to_csv.items():
            if ticker.upper() in url:
                return _MockResponse(csv_text)
        raise Exception(f"URL not in fixture map: {url}")
    return fn


def _error_http_fn(url: str) -> None:
    raise Exception("Connection refused")


# ── Deferred import helpers ────────────────────────────────────────────────────

def _diag_module():
    from app.services.intelligence.research_workers.vanguard_holdings_diagnostic_v1 import (
        DIAGNOSTIC_VERSION,
        CANONICAL_CANDIDATE,
        SUPPLEMENTAL_ONLY,
        MANUAL_RESEARCH_REQUIRED,
        REJECTED,
        run_vanguard_holdings_diagnostic,
    )
    return (
        DIAGNOSTIC_VERSION,
        CANONICAL_CANDIDATE,
        SUPPLEMENTAL_ONLY,
        MANUAL_RESEARCH_REQUIRED,
        REJECTED,
        run_vanguard_holdings_diagnostic,
    )


# ── Required per-ticker evidence fields ───────────────────────────────────────

_REQUIRED_EVIDENCE_FIELDS = {
    "ticker",
    "url_used",
    "access_pattern",
    "response_type",
    "fund_name_detected",
    "identity_verified",
    "identity_basis",
    "holdings_count",
    "holdings_weights_available",
    "as_of_date",
    "parse_status",
    "fetch_status",
    "failure_reason",
    "classification",
    "disqualifiers",
    "classification_rationale",
    "canonical_ready",
    "safe_for_decision",
}


# ── Tests: O1–O3 success paths ─────────────────────────────────────────────────

@pytest.mark.parametrize("ticker", ["VTI", "VOO", "VXUS"])
def test_o1_o3_success_canonical_candidate(ticker):
    """O1–O3: each default ticker → canonical_candidate when all S-grade criteria met."""
    (DIAGNOSTIC_VERSION, CANONICAL_CANDIDATE, SUPPLEMENTAL_ONLY,
     MANUAL_RESEARCH_REQUIRED, REJECTED, run) = _diag_module()

    http_fn = _make_http_fn({ticker: _success_csv(ticker)})
    result = run([ticker], http_get_fn=http_fn)

    assert result["safe_for_decision"] is False
    assert result["canonical_ready"] is False
    assert result["tickers_requested"] == 1

    entry = result["per_ticker"][0]
    assert entry["ticker"] == ticker
    assert entry["classification"] == CANONICAL_CANDIDATE
    assert entry["disqualifiers"] == []
    assert entry["canonical_ready"] is False
    assert entry["safe_for_decision"] is False
    assert entry["identity_verified"] is True
    assert entry["as_of_date"] is not None
    assert entry["holdings_weights_available"] is True
    assert entry["holdings_count"] >= 1


# ── Test O4: missing as_of_date → supplemental_only ───────────────────────────

def test_o4_missing_date_supplemental_only():
    """O4: CSV without as_of_date → supplemental_only with as_of_date_missing."""
    (_, CANONICAL_CANDIDATE, SUPPLEMENTAL_ONLY, MANUAL_RESEARCH_REQUIRED,
     REJECTED, run) = _diag_module()

    http_fn = _make_http_fn({"VTI": _no_date_csv("VTI")})
    result = run(["VTI"], http_get_fn=http_fn)

    entry = result["per_ticker"][0]
    assert entry["classification"] == SUPPLEMENTAL_ONLY
    assert "as_of_date_missing" in entry["disqualifiers"]
    assert entry["as_of_date"] is None
    assert entry["canonical_ready"] is False
    assert entry["safe_for_decision"] is False
    assert result["summary"]["supplemental_only_count"] == 1
    assert result["summary"]["canonical_candidate_count"] == 0


# ── Test O5: missing weights → supplemental_only ──────────────────────────────

def test_o5_missing_weights_supplemental_only():
    """O5: CSV without weight column → supplemental_only with weights_missing."""
    (_, CANONICAL_CANDIDATE, SUPPLEMENTAL_ONLY, MANUAL_RESEARCH_REQUIRED,
     REJECTED, run) = _diag_module()

    http_fn = _make_http_fn({"VOO": _no_weight_csv("VOO")})
    result = run(["VOO"], http_get_fn=http_fn)

    entry = result["per_ticker"][0]
    assert entry["classification"] == SUPPLEMENTAL_ONLY
    assert "weights_missing" in entry["disqualifiers"]
    assert entry["holdings_weights_available"] is False
    assert entry["canonical_ready"] is False
    assert entry["safe_for_decision"] is False
    assert result["summary"]["supplemental_only_count"] == 1
    assert result["summary"]["canonical_candidate_count"] == 0


# ── Test O6: identity mismatch → manual_research_required ─────────────────────

def test_o6_identity_mismatch_manual_research_required():
    """O6: Fund name in CSV does not match VXUS expected names → manual_research_required."""
    (_, CANONICAL_CANDIDATE, SUPPLEMENTAL_ONLY, MANUAL_RESEARCH_REQUIRED,
     REJECTED, run) = _diag_module()

    http_fn = _make_http_fn({"VXUS": _identity_mismatch_csv("VXUS")})
    result = run(["VXUS"], http_get_fn=http_fn)

    entry = result["per_ticker"][0]
    assert entry["classification"] == MANUAL_RESEARCH_REQUIRED
    assert "identity_not_proven" in entry["disqualifiers"]
    assert entry["identity_verified"] is False
    assert entry["canonical_ready"] is False
    assert entry["safe_for_decision"] is False
    assert result["summary"]["manual_research_required_count"] == 1


# ── Test O7: access failure → rejected ────────────────────────────────────────

def test_o7_access_failure_rejected():
    """O7: HTTP error during fetch → rejected with access_failure disqualifier."""
    (_, CANONICAL_CANDIDATE, SUPPLEMENTAL_ONLY, MANUAL_RESEARCH_REQUIRED,
     REJECTED, run) = _diag_module()

    result = run(["VTI"], http_get_fn=_error_http_fn)

    entry = result["per_ticker"][0]
    assert entry["classification"] == REJECTED
    assert "access_failure" in entry["disqualifiers"]
    assert entry["canonical_ready"] is False
    assert entry["safe_for_decision"] is False
    assert result["summary"]["rejected_count"] == 1


# ── Test O8: safe_for_decision always False ────────────────────────────────────

@pytest.mark.parametrize("ticker,csv_fn", [
    ("VTI",  lambda t: _success_csv(t)),
    ("VOO",  lambda t: _no_date_csv(t)),
    ("VXUS", lambda t: _no_weight_csv(t)),
])
def test_o8_safe_for_decision_always_false(ticker, csv_fn):
    """O8: safe_for_decision=False in every code path."""
    (_, _, _, _, _, run) = _diag_module()
    http_fn = _make_http_fn({ticker: csv_fn(ticker)})
    result = run([ticker], http_get_fn=http_fn)

    assert result["safe_for_decision"] is False
    assert result["per_ticker"][0]["safe_for_decision"] is False


# ── Test O9: canonical_ready always False ─────────────────────────────────────

@pytest.mark.parametrize("ticker,csv_fn", [
    ("VTI",  lambda t: _success_csv(t)),
    ("VOO",  lambda t: _no_date_csv(t)),
    ("VXUS", lambda t: _no_weight_csv(t)),
])
def test_o9_canonical_ready_always_false(ticker, csv_fn):
    """O9: canonical_ready=False even on canonical_candidate classification."""
    (_, _, _, _, _, run) = _diag_module()
    http_fn = _make_http_fn({ticker: csv_fn(ticker)})
    result = run([ticker], http_get_fn=http_fn)

    assert result["canonical_ready"] is False
    assert result["per_ticker"][0]["canonical_ready"] is False


# ── Test O10: mixed outcomes evaluated independently ──────────────────────────

def test_o10_mixed_outcomes_independent():
    """O10: VTI success, VOO missing date, VXUS access failure — all in one run."""
    (_, CANONICAL_CANDIDATE, SUPPLEMENTAL_ONLY, MANUAL_RESEARCH_REQUIRED,
     REJECTED, run) = _diag_module()

    def mixed_http_fn(url: str) -> _MockResponse:
        if "VTI" in url:
            return _MockResponse(_success_csv("VTI"))
        if "VOO" in url:
            return _MockResponse(_no_date_csv("VOO"))
        raise Exception("VXUS fetch refused")

    result = run(["VTI", "VOO", "VXUS"], http_get_fn=mixed_http_fn)

    assert result["tickers_requested"] == 3
    assert len(result["per_ticker"]) == 3

    by_ticker = {e["ticker"]: e for e in result["per_ticker"]}
    assert by_ticker["VTI"]["classification"] == CANONICAL_CANDIDATE
    assert by_ticker["VOO"]["classification"] == SUPPLEMENTAL_ONLY
    assert by_ticker["VXUS"]["classification"] == REJECTED

    assert result["summary"]["canonical_candidate_count"] == 1
    assert result["summary"]["supplemental_only_count"] == 1
    assert result["summary"]["rejected_count"] == 1


# ── Test O11: diagnostic_version ─────────────────────────────────────────────

def test_o11_diagnostic_version_present():
    """O11: diagnostic_version field is "stage9o_v1"."""
    (DIAGNOSTIC_VERSION, _, _, _, _, run) = _diag_module()

    http_fn = _make_http_fn({"VTI": _success_csv("VTI")})
    result = run(["VTI"], http_get_fn=http_fn)

    assert result["diagnostic_version"] == DIAGNOSTIC_VERSION
    assert result["diagnostic_version"] == "stage9o_v1"


# ── Test O12: required evidence fields present ────────────────────────────────

@pytest.mark.parametrize("ticker,csv_fn,http_fn_factory", [
    ("VTI",  _success_csv,          lambda t, c: _make_http_fn({t: c})),
    ("VOO",  _no_date_csv,          lambda t, c: _make_http_fn({t: c})),
    ("VXUS", _no_weight_csv,        lambda t, c: _make_http_fn({t: c})),
    ("VTI",  _identity_mismatch_csv, lambda t, c: _make_http_fn({t: c})),
])
def test_o12_required_evidence_fields_present(ticker, csv_fn, http_fn_factory):
    """O12: All required evidence fields are present in per-ticker entry."""
    (_, _, _, _, _, run) = _diag_module()
    csv_text = csv_fn(ticker)
    http_fn = http_fn_factory(ticker, csv_text)
    result = run([ticker], http_get_fn=http_fn)

    entry = result["per_ticker"][0]
    missing = _REQUIRED_EVIDENCE_FIELDS - set(entry.keys())
    assert not missing, f"Missing evidence fields: {missing}"


def test_o12_required_fields_on_access_failure():
    """O12b: Required evidence fields present even when access fails."""
    (_, _, _, _, _, run) = _diag_module()
    result = run(["VTI"], http_get_fn=_error_http_fn)
    entry = result["per_ticker"][0]
    missing = _REQUIRED_EVIDENCE_FIELDS - set(entry.keys())
    assert not missing, f"Missing evidence fields on failure: {missing}"


# ── Test O13: failure_reason on rejected ─────────────────────────────────────

def test_o13_failure_reason_on_rejected():
    """O13: Rejected entries include a non-empty failure_reason."""
    (_, _, _, _, REJECTED, run) = _diag_module()
    result = run(["VTI"], http_get_fn=_error_http_fn)
    entry = result["per_ticker"][0]
    assert entry["classification"] == REJECTED
    assert entry["failure_reason"]  # non-empty string


# ── Test O14: disqualifiers on supplemental ───────────────────────────────────

def test_o14_disqualifiers_on_supplemental():
    """O14: supplemental_only entries include non-empty disqualifiers."""
    (_, _, SUPPLEMENTAL_ONLY, _, _, run) = _diag_module()
    http_fn = _make_http_fn({"VTI": _no_date_csv("VTI")})
    result = run(["VTI"], http_get_fn=http_fn)
    entry = result["per_ticker"][0]
    assert entry["classification"] == SUPPLEMENTAL_ONLY
    assert isinstance(entry["disqualifiers"], list)
    assert len(entry["disqualifiers"]) >= 1


# ── Test O15: no fabricated date ─────────────────────────────────────────────

def test_o15_no_fabricated_date():
    """O15: as_of_date is None when absent — never inferred or fabricated."""
    (_, _, _, _, _, run) = _diag_module()
    http_fn = _make_http_fn({"VOO": _no_date_csv("VOO")})
    result = run(["VOO"], http_get_fn=http_fn)
    entry = result["per_ticker"][0]
    assert entry["as_of_date"] is None


# ── Test O16: no fabricated weights ──────────────────────────────────────────

def test_o16_no_fabricated_weights():
    """O16: holdings_weights_available is False when weights absent — not fabricated."""
    (_, _, _, _, _, run) = _diag_module()
    http_fn = _make_http_fn({"VXUS": _no_weight_csv("VXUS")})
    result = run(["VXUS"], http_get_fn=http_fn)
    entry = result["per_ticker"][0]
    assert entry["holdings_weights_available"] is False


# ── Test O17: identity_basis on success ──────────────────────────────────────

def test_o17_identity_basis_present_on_success():
    """O17: identity_basis is populated on the success (canonical_candidate) path."""
    (_, CANONICAL_CANDIDATE, _, _, _, run) = _diag_module()
    http_fn = _make_http_fn({"VTI": _success_csv("VTI")})
    result = run(["VTI"], http_get_fn=http_fn)
    entry = result["per_ticker"][0]
    assert entry["classification"] == CANONICAL_CANDIDATE
    assert entry["identity_basis"]  # non-empty


# ── Test O18: holdings_count is int >= 0 everywhere ──────────────────────────

@pytest.mark.parametrize("ticker,http_fn_factory", [
    ("VTI",  lambda: _make_http_fn({"VTI": _success_csv("VTI")})),
    ("VOO",  lambda: _make_http_fn({"VOO": _no_date_csv("VOO")})),
    ("VXUS", lambda: _error_http_fn),
])
def test_o18_holdings_count_non_negative_int(ticker, http_fn_factory):
    """O18: holdings_count is always an int >= 0 regardless of path."""
    (_, _, _, _, _, run) = _diag_module()
    http_fn = http_fn_factory() if callable(http_fn_factory()) else http_fn_factory
    if ticker == "VXUS":
        http_fn = _error_http_fn
    else:
        http_fn = http_fn_factory()
    result = run([ticker], http_get_fn=http_fn)
    entry = result["per_ticker"][0]
    assert isinstance(entry["holdings_count"], int)
    assert entry["holdings_count"] >= 0


# ── Test O19: url_used present for all tickers ────────────────────────────────

@pytest.mark.parametrize("ticker", ["VTI", "VOO", "VXUS"])
def test_o19_url_used_present(ticker):
    """O19: url_used field is populated with the Vanguard CSV URL for all paths."""
    (_, _, _, _, _, run) = _diag_module()
    result = run([ticker], http_get_fn=_error_http_fn)
    entry = result["per_ticker"][0]
    assert entry["url_used"]
    assert ticker in entry["url_used"]
    assert "vanguard" in entry["url_used"].lower()


# ── Test O20: batch == sum of singles ─────────────────────────────────────────

def test_o20_batch_equals_independent_singles():
    """O20: batch run classifies each ticker the same as running each alone."""
    (_, _, _, _, _, run) = _diag_module()

    http_fn = _make_http_fn({
        "VTI":  _success_csv("VTI"),
        "VOO":  _success_csv("VOO"),
        "VXUS": _success_csv("VXUS"),
    })

    batch = run(["VTI", "VOO", "VXUS"], http_get_fn=http_fn)
    batch_by_ticker = {e["ticker"]: e["classification"] for e in batch["per_ticker"]}

    for ticker in ["VTI", "VOO", "VXUS"]:
        single = run([ticker], http_get_fn=_make_http_fn({ticker: _success_csv(ticker)}))
        assert single["per_ticker"][0]["classification"] == batch_by_ticker[ticker], (
            f"Batch and single disagree for {ticker}"
        )


# ── Test O21: summary counts consistent ──────────────────────────────────────

def test_o21_summary_counts_consistent_with_per_ticker():
    """O21: summary counts match the per_ticker classification distribution."""
    (_, CANONICAL_CANDIDATE, SUPPLEMENTAL_ONLY, MANUAL_RESEARCH_REQUIRED,
     REJECTED, run) = _diag_module()

    def mixed_http_fn(url: str) -> _MockResponse:
        if "VTI" in url:
            return _MockResponse(_success_csv("VTI"))
        if "VOO" in url:
            return _MockResponse(_no_date_csv("VOO"))
        if "VXUS" in url:
            return _MockResponse(_identity_mismatch_csv("VXUS"))
        raise Exception("unexpected ticker")

    result = run(["VTI", "VOO", "VXUS"], http_get_fn=mixed_http_fn)
    summary = result["summary"]

    per_ticker_counts = {
        "canonical_candidate": 0,
        "supplemental_only": 0,
        "manual_research_required": 0,
        "rejected": 0,
    }
    for e in result["per_ticker"]:
        key = f"{e['classification']}"
        if key in per_ticker_counts:
            per_ticker_counts[key] += 1

    assert summary["canonical_candidate_count"] == per_ticker_counts["canonical_candidate"]
    assert summary["supplemental_only_count"] == per_ticker_counts["supplemental_only"]
    assert summary["manual_research_required_count"] == per_ticker_counts["manual_research_required"]
    assert summary["rejected_count"] == per_ticker_counts["rejected"]


# ── Test O22: diagnostics_only and artifact_writes ───────────────────────────

@pytest.mark.parametrize("ticker,csv_fn_or_error", [
    ("VTI", _success_csv),
    ("VOO", _no_date_csv),
    ("VTI", None),  # None → error path
])
def test_o22_diagnostics_only_and_no_artifact_writes(ticker, csv_fn_or_error):
    """O22: diagnostics_only=True and artifact_writes=0 on every result."""
    (_, _, _, _, _, run) = _diag_module()
    if csv_fn_or_error is None:
        result = run([ticker], http_get_fn=_error_http_fn)
    else:
        result = run([ticker], http_get_fn=_make_http_fn({ticker: csv_fn_or_error(ticker)}))
    assert result["diagnostics_only"] is True
    assert result["artifact_writes"] == 0


# ── Test O23: policy_unchanged and visible_snapshot_unchanged ─────────────────

def test_o23_policy_and_snapshot_unchanged():
    """O23: policy_unchanged=True and visible_snapshot_unchanged=True always."""
    (_, _, _, _, _, run) = _diag_module()
    http_fn = _make_http_fn({"VTI": _success_csv("VTI")})
    result = run(["VTI"], http_get_fn=http_fn)
    assert result["policy_unchanged"] is True
    assert result["visible_snapshot_unchanged"] is True


# ── Test O24: empty response body → rejected ─────────────────────────────────

def test_o24_empty_response_body_rejected():
    """O24: Empty CSV content → rejected."""
    (_, _, _, _, REJECTED, run) = _diag_module()

    def empty_http_fn(url: str) -> _MockResponse:
        return _MockResponse("")

    result = run(["VTI"], http_get_fn=empty_http_fn)
    entry = result["per_ticker"][0]
    assert entry["classification"] == REJECTED
    assert entry["canonical_ready"] is False


# ── Test O25: unrecognised CSV layout → rejected ─────────────────────────────

def test_o25_unrecognised_csv_layout_rejected():
    """O25: CSV with no recognisable column headers → rejected (source_shape_changed)."""
    (_, _, _, _, REJECTED, run) = _diag_module()

    broken_csv = "col_a,col_b,col_c\nfoo,bar,baz\nfoo2,bar2,baz2\n"

    def broken_http_fn(url: str) -> _MockResponse:
        return _MockResponse(broken_csv)

    result = run(["VTI"], http_get_fn=broken_http_fn)
    entry = result["per_ticker"][0]
    assert entry["classification"] == REJECTED
    assert entry["canonical_ready"] is False


# ── Tests O26–O28: fund_name_detected evidence contract ──────────────────────

@pytest.mark.parametrize("ticker", ["VTI", "VOO", "VXUS"])
def test_o26_fund_name_detected_exact_on_success(ticker):
    """O26: fund_name_detected equals the raw fixture fund name exactly on success path."""
    (_, CANONICAL_CANDIDATE, _, _, _, run) = _diag_module()

    http_fn = _make_http_fn({ticker: _success_csv(ticker)})
    result = run([ticker], http_get_fn=http_fn)

    entry = result["per_ticker"][0]
    assert entry["classification"] == CANONICAL_CANDIDATE
    assert entry["fund_name_detected"] == _FUND_NAMES[ticker], (
        f"Expected {_FUND_NAMES[ticker]!r}, got {entry['fund_name_detected']!r}"
    )
    assert entry["safe_for_decision"] is False
    assert entry["canonical_ready"] is False


def test_o27_fund_name_detected_raw_mismatch_on_identity_failure():
    """O27: fund_name_detected is the raw mismatched CSV name on identity_not_proven path."""
    (_, _, _, MANUAL_RESEARCH_REQUIRED, _, run) = _diag_module()

    http_fn = _make_http_fn({"VXUS": _identity_mismatch_csv("VXUS")})
    result = run(["VXUS"], http_get_fn=http_fn)

    entry = result["per_ticker"][0]
    assert entry["classification"] == MANUAL_RESEARCH_REQUIRED
    assert entry["fund_name_detected"] == "XYZ Global Asset Group Ltd", (
        f"Expected raw mismatch name, got {entry['fund_name_detected']!r}"
    )
    assert entry["identity_basis"] != entry["fund_name_detected"], (
        "identity_basis should be an explanation string, not the raw fund name"
    )
    assert entry["safe_for_decision"] is False
    assert entry["canonical_ready"] is False


def test_o28_fund_name_detected_none_on_access_failure():
    """O28: fund_name_detected is None when HTTP access fails — nothing was parsed."""
    (_, _, _, _, REJECTED, run) = _diag_module()

    result = run(["VTI"], http_get_fn=_error_http_fn)

    entry = result["per_ticker"][0]
    assert entry["classification"] == REJECTED
    assert entry["fund_name_detected"] is None
    assert entry["safe_for_decision"] is False
    assert entry["canonical_ready"] is False


# ── Tests O29–O36: issuer eligibility guard ───────────────────────────────────

def _network_call_detector(url: str) -> None:
    """Raises if called — proves no network call was made for rejected issuers."""
    raise AssertionError(f"Network call must not be made for unsupported issuers: {url}")


def test_o29_schd_rejected_before_network_call():
    """O29: SCHD (Schwab ETF) is rejected before any network call — unsupported_issuer_for_provider."""
    (_, _, _, _, REJECTED, run) = _diag_module()

    result = run(["SCHD"], http_get_fn=_network_call_detector)

    entry = result["per_ticker"][0]
    assert entry["classification"] == REJECTED
    assert entry["failure_reason"] == "unsupported_issuer_for_provider"
    assert "unsupported_issuer_for_provider" in entry["disqualifiers"]
    assert entry["canonical_ready"] is False
    assert entry["safe_for_decision"] is False
    assert result["summary"]["rejected_count"] == 1
    # url_used must be None — no Vanguard URL is constructed for unsupported issuers
    assert entry["url_used"] is None, (
        f"url_used must be None for unsupported issuer SCHD, got {entry['url_used']!r}"
    )
    url_str = entry["url_used"] or ""
    assert "vanguard" not in url_str.lower()
    assert "investor.vanguard.com" not in url_str.lower()
    assert "QuantDataFundHoldings" not in url_str


def test_o30_schd_output_is_deterministic():
    """O30: SCHD eligibility rejection is deterministic — same output on repeated calls."""
    (_, _, _, _, REJECTED, run) = _diag_module()

    result1 = run(["SCHD"], http_get_fn=_network_call_detector)
    result2 = run(["SCHD"], http_get_fn=_network_call_detector)

    e1 = result1["per_ticker"][0]
    e2 = result2["per_ticker"][0]
    assert e1["classification"] == e2["classification"] == REJECTED
    assert e1["failure_reason"] == e2["failure_reason"] == "unsupported_issuer_for_provider"
    assert e1["canonical_ready"] is False
    assert e2["canonical_ready"] is False
    assert e1["safe_for_decision"] is False
    assert e2["safe_for_decision"] is False


def test_o31_unsupported_issuer_required_fields_all_present():
    """O31: All required evidence fields are present on unsupported-issuer rejection path."""
    (_, _, _, _, _, run) = _diag_module()

    result = run(["SCHD"], http_get_fn=_network_call_detector)
    entry = result["per_ticker"][0]

    missing = _REQUIRED_EVIDENCE_FIELDS - set(entry.keys())
    assert not missing, f"Missing evidence fields on unsupported-issuer path: {missing}"


def test_o32_unsupported_issuer_zero_holdings():
    """O32: Unsupported issuer returns holdings_count=0 and holdings_weights_available=False."""
    (_, _, _, _, REJECTED, run) = _diag_module()

    result = run(["SCHD"], http_get_fn=_network_call_detector)
    entry = result["per_ticker"][0]

    assert entry["holdings_count"] == 0
    assert entry["holdings_weights_available"] is False
    assert entry["as_of_date"] is None
    assert entry["identity_verified"] is False


def test_o33_unsupported_issuer_summary_count_correct():
    """O33: Unsupported issuer increments rejected_count in summary; other counts zero."""
    (_, _, _, _, REJECTED, run) = _diag_module()

    result = run(["SCHD"], http_get_fn=_network_call_detector)

    assert result["summary"]["rejected_count"] == 1
    assert result["summary"]["canonical_candidate_count"] == 0
    assert result["summary"]["supplemental_only_count"] == 0
    assert result["summary"]["manual_research_required_count"] == 0


def test_o34_unsupported_issuer_mixed_with_valid_ticker():
    """O34: SCHD rejected before network; VTI proceeds normally in the same run."""
    (_, CANONICAL_CANDIDATE, _, _, REJECTED, run) = _diag_module()

    http_fn = _make_http_fn({"VTI": _success_csv("VTI")})

    def guarded_http_fn(url: str) -> _MockResponse:
        if "SCHD" in url:
            raise AssertionError(f"SCHD must not reach the network: {url}")
        return http_fn(url)

    result = run(["VTI", "SCHD"], http_get_fn=guarded_http_fn)

    by_ticker = {e["ticker"]: e for e in result["per_ticker"]}
    assert by_ticker["VTI"]["classification"] == CANONICAL_CANDIDATE
    assert by_ticker["SCHD"]["classification"] == REJECTED
    assert by_ticker["SCHD"]["failure_reason"] == "unsupported_issuer_for_provider"
    assert result["summary"]["canonical_candidate_count"] == 1
    assert result["summary"]["rejected_count"] == 1


def test_o35_unsupported_issuer_policy_invariants():
    """O35: diagnostics_only, policy_unchanged, visible_snapshot_unchanged on unsupported-issuer path."""
    (_, _, _, _, _, run) = _diag_module()

    result = run(["SCHD"], http_get_fn=_network_call_detector)

    assert result["diagnostics_only"] is True
    assert result["artifact_writes"] == 0
    assert result["policy_unchanged"] is True
    assert result["visible_snapshot_unchanged"] is True
    assert result["canonical_ready"] is False
    assert result["safe_for_decision"] is False


def test_o36_arbitrary_non_vanguard_ticker_rejected_before_network():
    """O36: Arbitrary non-Vanguard ticker (SPY) is rejected before network call, url_used=None."""
    (_, _, _, _, REJECTED, run) = _diag_module()

    result = run(["SPY"], http_get_fn=_network_call_detector)

    entry = result["per_ticker"][0]
    assert entry["classification"] == REJECTED
    assert entry["failure_reason"] == "unsupported_issuer_for_provider"
    assert entry["canonical_ready"] is False
    assert entry["safe_for_decision"] is False
    # No Vanguard URL produced for unsupported issuers
    assert entry["url_used"] is None, (
        f"url_used must be None for unsupported issuer SPY, got {entry['url_used']!r}"
    )
    url_str = entry["url_used"] or ""
    assert "vanguard" not in url_str.lower()
    assert "QuantDataFundHoldings" not in url_str
