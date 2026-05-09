"""Phase 14C.1 — EPS payload shape extractor tests.

Covers every supported payload shape, all skip scenarios, leakage prevention,
hard-lock invariants, and static import / write safety.

Payload shapes tested:
  Shape A — explicit fiscal_period=="FY" + fiscal_year present
  Shape B — explicit fiscal_period=="FY" + fiscal_year absent → filed year
  Shape C — fiscal_period absent + form=="10-K" → FY-equivalent
  Shape C+fy — fiscal_period absent + form=="10-K" + fiscal_year present

Skip scenarios tested:
  - wrong_tag (non-EPS tag)
  - not_source_linked (has_source==False)
  - missing_numeric_value (value absent or malformed)
  - not_fy_period (Q1/Q2/Q3 fiscal_period, or absent+10-Q, or explicit non-FY)
  - missing_fiscal_year (FY signal present but no year available)

Invariants tested:
  - No raw EPS value in skip results
  - No IO/DB/provider/LLM imports
  - No DB write methods
  - Diluted preferred over basic (router-level, not extractor-level)
  - Extractor is pure: no side effects, deterministic output
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_EXTRACTOR_PATH = (
    Path(__file__).parent.parent
    / "app/services/intelligence/v3/eps_payload_extractor_v1.py"
)
_ROUTER_PATH = (
    Path(__file__).parent.parent / "app/routers/diagnostics.py"
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _extract(payload: dict, *, has_source: bool = True):
    from app.services.intelligence.v3.eps_payload_extractor_v1 import (
        extract_fy_eps_observation_from_payload,
    )
    return extract_fy_eps_observation_from_payload(payload, has_source=has_source)


def _base_diluted_fy(**kwargs) -> dict:
    """Minimal Shape A payload for EarningsPerShareDiluted."""
    base = {
        "claim": "sec_companyfact_observed",
        "tag": "EarningsPerShareDiluted",
        "value": 4.5,
        "unit": "USD/shares",
        "form": "10-K",
        "filed": "2024-02-15",
        "fiscal_period": "FY",
        "fiscal_year": 2023,
        "accession_number": "0000320193-24-000001",
    }
    base.update(kwargs)
    return base


def _base_basic_fy(**kwargs) -> dict:
    """Minimal Shape A payload for EarningsPerShareBasic."""
    base = _base_diluted_fy(**kwargs)
    base["tag"] = "EarningsPerShareBasic"
    return base


# ── Shape A: explicit FY period + fiscal_year ─────────────────────────────────

class TestShapeA:
    """fiscal_period=="FY" AND fiscal_year present AND value present."""

    def test_diluted_computable(self):
        r = _extract(_base_diluted_fy())
        assert r.skip_reason == ""
        assert r.tag == "EarningsPerShareDiluted"
        assert r.ordering_year == 2023
        assert r.eps_value == pytest.approx(4.5)
        assert r.fy_source == "fiscal_period_fy"
        assert r.year_source == "fiscal_year"

    def test_basic_computable(self):
        r = _extract(_base_basic_fy())
        assert r.skip_reason == ""
        assert r.tag == "EarningsPerShareBasic"
        assert r.ordering_year == 2023
        assert r.eps_value == pytest.approx(4.5)

    def test_negative_eps_computable(self):
        r = _extract(_base_diluted_fy(value=-2.5))
        assert r.skip_reason == ""
        assert r.eps_value == pytest.approx(-2.5)

    def test_zero_eps_computable(self):
        # Extractor does not reject zero EPS — the pure compute module does.
        r = _extract(_base_diluted_fy(value=0.0))
        assert r.skip_reason == ""
        assert r.eps_value == pytest.approx(0.0)

    def test_integer_value_accepted(self):
        r = _extract(_base_diluted_fy(value=5))
        assert r.skip_reason == ""
        assert r.eps_value == pytest.approx(5.0)

    def test_fiscal_year_as_string_int(self):
        r = _extract(_base_diluted_fy(fiscal_year="2022"))
        assert r.skip_reason == ""
        assert r.ordering_year == 2022


# ── Shape B: fiscal_period=="FY" + fiscal_year absent ─────────────────────────

class TestShapeB:
    """fiscal_period=="FY" AND fiscal_year absent → filed year as ordering key."""

    def test_filed_year_used_when_fiscal_year_absent(self):
        sp = _base_diluted_fy()
        del sp["fiscal_year"]
        r = _extract(sp)
        assert r.skip_reason == ""
        assert r.ordering_year == 2024  # from "2024-02-15"
        assert r.year_source == "filed_year"
        assert r.fy_source == "fiscal_period_fy"

    def test_filed_year_used_when_fiscal_year_none(self):
        sp = _base_diluted_fy(fiscal_year=None)
        r = _extract(sp)
        assert r.skip_reason == ""
        assert r.ordering_year == 2024
        assert r.year_source == "filed_year"

    def test_malformed_fiscal_year_falls_back_to_filed(self):
        sp = _base_diluted_fy(fiscal_year="not-a-year")
        r = _extract(sp)
        assert r.skip_reason == ""
        assert r.ordering_year == 2024
        assert r.year_source == "filed_year"

    def test_basic_shape_b(self):
        sp = _base_basic_fy()
        del sp["fiscal_year"]
        r = _extract(sp)
        assert r.skip_reason == ""
        assert r.tag == "EarningsPerShareBasic"
        assert r.ordering_year == 2024


# ── Shape C: fiscal_period absent + form=="10-K" ──────────────────────────────

class TestShapeC:
    """fiscal_period absent/None AND form=="10-K" — FY-equivalent by definition."""

    def _sp_no_fp(self, **kwargs) -> dict:
        sp = _base_diluted_fy(**kwargs)
        sp.pop("fiscal_period", None)
        return sp

    def test_form_10k_without_fiscal_period(self):
        sp = self._sp_no_fp()
        r = _extract(sp)
        assert r.skip_reason == ""
        assert r.fy_source == "form_10k"
        assert r.ordering_year == 2023  # fiscal_year present
        assert r.year_source == "fiscal_year"

    def test_form_10k_no_fiscal_period_no_fiscal_year(self):
        sp = self._sp_no_fp()
        del sp["fiscal_year"]
        r = _extract(sp)
        assert r.skip_reason == ""
        assert r.fy_source == "form_10k"
        assert r.ordering_year == 2024  # from filed date
        assert r.year_source == "filed_year"

    def test_form_10k_fiscal_period_none(self):
        sp = _base_diluted_fy(fiscal_period=None)
        r = _extract(sp)
        assert r.skip_reason == ""
        assert r.fy_source == "form_10k"

    def test_basic_form_10k_no_fiscal_period(self):
        sp = _base_basic_fy()
        sp.pop("fiscal_period", None)
        r = _extract(sp)
        assert r.skip_reason == ""
        assert r.tag == "EarningsPerShareBasic"

    def test_form_10k_lowercase_accepted(self):
        sp = self._sp_no_fp(form="10-k")
        r = _extract(sp)
        assert r.skip_reason == ""
        assert r.fy_source == "form_10k"


# ── Skip: wrong tag ────────────────────────────────────────────────────────────

class TestSkipWrongTag:
    def test_non_eps_tag_skipped(self):
        from app.services.intelligence.v3.eps_payload_extractor_v1 import SKIP_WRONG_TAG
        sp = _base_diluted_fy(tag="NetIncomeLoss")
        r = _extract(sp)
        assert r.skip_reason == SKIP_WRONG_TAG

    def test_stockholders_equity_skipped(self):
        from app.services.intelligence.v3.eps_payload_extractor_v1 import SKIP_WRONG_TAG
        sp = _base_diluted_fy(tag="StockholdersEquity")
        r = _extract(sp)
        assert r.skip_reason == SKIP_WRONG_TAG

    def test_revenues_skipped(self):
        from app.services.intelligence.v3.eps_payload_extractor_v1 import SKIP_WRONG_TAG
        sp = _base_diluted_fy(tag="Revenues")
        r = _extract(sp)
        assert r.skip_reason == SKIP_WRONG_TAG

    def test_empty_tag_skipped(self):
        from app.services.intelligence.v3.eps_payload_extractor_v1 import SKIP_WRONG_TAG
        sp = _base_diluted_fy(tag="")
        r = _extract(sp)
        assert r.skip_reason == SKIP_WRONG_TAG

    def test_skip_result_has_no_raw_eps(self):
        from app.services.intelligence.v3.eps_payload_extractor_v1 import SKIP_WRONG_TAG
        sp = _base_diluted_fy(tag="Revenues")
        r = _extract(sp)
        assert r.skip_reason == SKIP_WRONG_TAG
        assert r.eps_value == 0.0  # default sentinel — not raw EPS


# ── Skip: not source-linked ────────────────────────────────────────────────────

class TestSkipNotSourceLinked:
    def test_not_source_linked_skipped(self):
        from app.services.intelligence.v3.eps_payload_extractor_v1 import SKIP_NOT_SOURCE_LINKED
        r = _extract(_base_diluted_fy(), has_source=False)
        assert r.skip_reason == SKIP_NOT_SOURCE_LINKED

    def test_source_linked_accepted(self):
        r = _extract(_base_diluted_fy(), has_source=True)
        assert r.skip_reason == ""

    def test_not_source_linked_no_raw_eps(self):
        from app.services.intelligence.v3.eps_payload_extractor_v1 import SKIP_NOT_SOURCE_LINKED
        r = _extract(_base_diluted_fy(), has_source=False)
        assert r.skip_reason == SKIP_NOT_SOURCE_LINKED
        assert r.eps_value == 0.0


# ── Skip: missing / malformed numeric value ───────────────────────────────────

class TestSkipMissingValue:
    def test_value_none_skipped(self):
        from app.services.intelligence.v3.eps_payload_extractor_v1 import SKIP_MISSING_VALUE
        sp = _base_diluted_fy()
        sp["value"] = None
        r = _extract(sp)
        assert r.skip_reason == SKIP_MISSING_VALUE

    def test_value_absent_skipped(self):
        from app.services.intelligence.v3.eps_payload_extractor_v1 import SKIP_MISSING_VALUE
        sp = _base_diluted_fy()
        del sp["value"]
        r = _extract(sp)
        assert r.skip_reason == SKIP_MISSING_VALUE

    def test_value_string_non_numeric_skipped(self):
        from app.services.intelligence.v3.eps_payload_extractor_v1 import SKIP_MISSING_VALUE
        sp = _base_diluted_fy(value="not-a-number")
        r = _extract(sp)
        assert r.skip_reason == SKIP_MISSING_VALUE

    def test_value_dict_skipped(self):
        from app.services.intelligence.v3.eps_payload_extractor_v1 import SKIP_MISSING_VALUE
        sp = _base_diluted_fy(value={"nested": "bad"})
        r = _extract(sp)
        assert r.skip_reason == SKIP_MISSING_VALUE

    def test_missing_value_no_raw_eps_leakage(self):
        from app.services.intelligence.v3.eps_payload_extractor_v1 import SKIP_MISSING_VALUE
        sp = _base_diluted_fy()
        del sp["value"]
        r = _extract(sp)
        assert r.skip_reason == SKIP_MISSING_VALUE
        assert r.eps_value == 0.0


# ── Skip: not FY period ───────────────────────────────────────────────────────

class TestSkipNotFY:
    def test_q1_fiscal_period_skipped(self):
        from app.services.intelligence.v3.eps_payload_extractor_v1 import SKIP_NOT_FY
        sp = _base_diluted_fy(fiscal_period="Q1")
        r = _extract(sp)
        assert r.skip_reason == SKIP_NOT_FY

    def test_q2_fiscal_period_skipped(self):
        from app.services.intelligence.v3.eps_payload_extractor_v1 import SKIP_NOT_FY
        sp = _base_diluted_fy(fiscal_period="Q2")
        r = _extract(sp)
        assert r.skip_reason == SKIP_NOT_FY

    def test_q3_fiscal_period_skipped(self):
        from app.services.intelligence.v3.eps_payload_extractor_v1 import SKIP_NOT_FY
        sp = _base_diluted_fy(fiscal_period="Q3")
        r = _extract(sp)
        assert r.skip_reason == SKIP_NOT_FY

    def test_10q_form_without_fiscal_period_skipped(self):
        from app.services.intelligence.v3.eps_payload_extractor_v1 import SKIP_NOT_FY
        sp = _base_diluted_fy(form="10-Q")
        sp.pop("fiscal_period", None)
        r = _extract(sp)
        assert r.skip_reason == SKIP_NOT_FY

    def test_10q_form_q3_period_skipped(self):
        from app.services.intelligence.v3.eps_payload_extractor_v1 import SKIP_NOT_FY
        sp = _base_diluted_fy(form="10-Q", fiscal_period="Q3")
        r = _extract(sp)
        assert r.skip_reason == SKIP_NOT_FY

    def test_explicit_q1_with_10k_form_skipped(self):
        # Explicit non-FY fiscal_period takes precedence over form.
        from app.services.intelligence.v3.eps_payload_extractor_v1 import SKIP_NOT_FY
        sp = _base_diluted_fy(fiscal_period="Q1", form="10-K")
        r = _extract(sp)
        assert r.skip_reason == SKIP_NOT_FY

    def test_absent_period_absent_form_skipped(self):
        # No FY signal at all.
        from app.services.intelligence.v3.eps_payload_extractor_v1 import SKIP_NOT_FY
        sp = _base_diluted_fy()
        sp.pop("fiscal_period", None)
        sp.pop("form", None)
        r = _extract(sp)
        assert r.skip_reason == SKIP_NOT_FY

    def test_absent_period_empty_form_skipped(self):
        from app.services.intelligence.v3.eps_payload_extractor_v1 import SKIP_NOT_FY
        sp = _base_diluted_fy(form="")
        sp.pop("fiscal_period", None)
        r = _extract(sp)
        assert r.skip_reason == SKIP_NOT_FY


# ── Skip: missing fiscal year (no year signal available) ─────────────────────

class TestSkipMissingYear:
    def test_fy_period_no_fiscal_year_no_filed_skipped(self):
        from app.services.intelligence.v3.eps_payload_extractor_v1 import SKIP_MISSING_YEAR
        sp = _base_diluted_fy()
        del sp["fiscal_year"]
        sp["filed"] = ""  # empty filed → no year
        r = _extract(sp)
        assert r.skip_reason == SKIP_MISSING_YEAR

    def test_fy_period_no_fiscal_year_filed_absent_skipped(self):
        from app.services.intelligence.v3.eps_payload_extractor_v1 import SKIP_MISSING_YEAR
        sp = _base_diluted_fy()
        del sp["fiscal_year"]
        sp.pop("filed", None)
        r = _extract(sp)
        assert r.skip_reason == SKIP_MISSING_YEAR

    def test_fy_period_no_fiscal_year_filed_too_short_skipped(self):
        from app.services.intelligence.v3.eps_payload_extractor_v1 import SKIP_MISSING_YEAR
        sp = _base_diluted_fy()
        del sp["fiscal_year"]
        sp["filed"] = "202"  # < 4 chars
        r = _extract(sp)
        assert r.skip_reason == SKIP_MISSING_YEAR

    def test_form_10k_no_fiscal_year_no_filed_skipped(self):
        from app.services.intelligence.v3.eps_payload_extractor_v1 import SKIP_MISSING_YEAR
        sp = _base_diluted_fy()
        sp.pop("fiscal_period", None)
        del sp["fiscal_year"]
        sp["filed"] = ""
        r = _extract(sp)
        assert r.skip_reason == SKIP_MISSING_YEAR


# ── Diluted preferred over basic (router-level ordering) ─────────────────────

class TestDilutedPreferredOrdering:
    """The extractor extracts each fact independently; the router picks the
    most-recent ordering_year per (ticker, tag). Tests here verify the
    extractor correctly returns the right tag so the router can apply diluted
    preference."""

    def test_diluted_tag_returned_correctly(self):
        r = _extract(_base_diluted_fy())
        assert r.tag == "EarningsPerShareDiluted"

    def test_basic_tag_returned_correctly(self):
        r = _extract(_base_basic_fy())
        assert r.tag == "EarningsPerShareBasic"

    def test_later_fiscal_year_has_higher_ordering_year(self):
        r_old = _extract(_base_diluted_fy(fiscal_year=2021))
        r_new = _extract(_base_diluted_fy(fiscal_year=2023))
        assert r_new.ordering_year > r_old.ordering_year


# ── No raw EPS / payload leakage in response ─────────────────────────────────

class TestNoRawLeakage:
    def test_skip_result_fields_contain_no_raw_eps(self):
        from app.services.intelligence.v3.eps_payload_extractor_v1 import SKIP_NOT_FY
        sp = _base_diluted_fy(fiscal_period="Q1", value=99.99)
        r = _extract(sp)
        assert r.skip_reason == SKIP_NOT_FY
        # eps_value must be the default sentinel (0.0), not the raw value.
        assert r.eps_value == 0.0
        assert r.tag == ""
        assert r.ordering_year == 0

    def test_result_fields_have_no_ticker_context(self):
        # EpsExtractionResult has no ticker field — no per-ticker leakage possible.
        from app.services.intelligence.v3.eps_payload_extractor_v1 import EpsExtractionResult
        fields = EpsExtractionResult.__dataclass_fields__
        assert "ticker" not in fields
        assert "source_url" not in fields
        assert "accession_number" not in fields
        assert "structured_payload" not in fields

    def test_result_has_no_dict_fields(self):
        # No dict-typed fields that could carry per-ticker maps.
        from app.services.intelligence.v3.eps_payload_extractor_v1 import EpsExtractionResult
        r = EpsExtractionResult()
        for v in r.__dict__.values():
            assert not isinstance(v, dict), "unexpected dict field in EpsExtractionResult"


# ── Schema version constant ───────────────────────────────────────────────────

class TestSchemaVersion:
    def test_schema_version_constant(self):
        from app.services.intelligence.v3.eps_payload_extractor_v1 import (
            EPS_EXTRACTION_SCHEMA_VERSION,
        )
        assert EPS_EXTRACTION_SCHEMA_VERSION == "eps_payload_extractor_v1"

    def test_router_response_includes_schema_version(self):
        src = _ROUTER_PATH.read_text()
        assert "eps_extraction_schema_version" in src
        assert "EPS_EXTRACTION_SCHEMA_VERSION" in src

    def test_router_imports_extractor(self):
        src = _ROUTER_PATH.read_text()
        assert "eps_payload_extractor_v1" in src
        assert "extract_fy_eps_observation_from_payload" in src


# ── Router response includes new diagnostic fields ───────────────────────────

class TestRouterDiagnosticFields:
    def _src(self):
        return _ROUTER_PATH.read_text()

    def test_shape_checked_count_in_response(self):
        assert "eps_payload_shape_checked_count" in self._src()

    def test_shape_computable_count_in_response(self):
        assert "eps_payload_shape_computable_count" in self._src()

    def test_skipped_fiscal_period_count_in_response(self):
        assert "skipped_eps_missing_fiscal_period_count" in self._src()

    def test_skipped_fiscal_year_count_in_response(self):
        assert "skipped_eps_missing_fiscal_year_count" in self._src()

    def test_skipped_numeric_value_count_in_response(self):
        assert "skipped_eps_missing_numeric_value_count" in self._src()

    def test_skipped_not_source_linked_count_in_response(self):
        assert "skipped_eps_not_source_linked_count" in self._src()


# ── Hard locks unchanged ──────────────────────────────────────────────────────

class TestHardLocksPreserved:
    """Extractor changes must not affect Phase 14C hard-lock fields."""

    def test_extractor_has_no_safe_for_decision_field(self):
        from app.services.intelligence.v3.eps_payload_extractor_v1 import EpsExtractionResult
        assert "safe_for_decision" not in EpsExtractionResult.__dataclass_fields__

    def test_extractor_has_no_priceband_field(self):
        from app.services.intelligence.v3.eps_payload_extractor_v1 import EpsExtractionResult
        assert "priceband_produced" not in EpsExtractionResult.__dataclass_fields__

    def test_extractor_has_no_visible_decision_field(self):
        from app.services.intelligence.v3.eps_payload_extractor_v1 import EpsExtractionResult
        assert "visible_decision_changed" not in EpsExtractionResult.__dataclass_fields__

    def test_router_still_asserts_safe_for_decision_false(self):
        src = _ROUTER_PATH.read_text()
        assert "safe_for_decision" in src


# ── Static import safety ──────────────────────────────────────────────────────

class TestStaticImportSafety:
    def _module_src(self):
        return _EXTRACTOR_PATH.read_text()

    def _ast(self):
        return ast.parse(self._module_src())

    def _imports(self):
        names = []
        for node in ast.walk(self._ast()):
            if isinstance(node, ast.Import):
                names.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names.append(node.module)
        return names

    def test_no_provider_or_llm_or_http_imports(self):
        forbidden = {"yfinance", "openai", "anthropic", "httpx", "requests",
                     "urllib", "aiohttp"}
        for m in self._imports():
            top = m.split(".")[0]
            assert top not in forbidden, f"forbidden import: {m}"

    def test_no_db_imports(self):
        forbidden = {"supabase", "psycopg2", "sqlalchemy"}
        for m in self._imports():
            top = m.split(".")[0]
            assert top not in forbidden, f"forbidden DB import: {m}"

    def test_no_decision_policy_imports(self):
        for m in self._imports():
            assert "decision_policy" not in m
            assert "decision_contracts" not in m

    def test_no_priceband_run_v3_decisioninputv3_references(self):
        forbidden_names = {"DecisionInputV3", "PriceBand", "run_v3", "decide"}
        for node in ast.walk(self._ast()):
            if isinstance(node, ast.Name):
                assert node.id not in forbidden_names
            if isinstance(node, ast.Attribute):
                assert node.attr not in forbidden_names

    def test_no_db_write_method_calls(self):
        forbidden = {"insert", "upsert", "update", "delete"}
        for node in ast.walk(self._ast()):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in forbidden, (
                    f"DB write method .{node.func.attr}() in extractor"
                )

    def test_no_intel_v3_snapshot_table_references(self):
        for node in ast.walk(self._ast()):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value != "intel_v3_snapshots"


# ── Determinism and fail-safe ─────────────────────────────────────────────────

class TestDeterminismAndFailSafe:
    def test_same_input_same_output(self):
        sp = _base_diluted_fy()
        r1 = _extract(sp)
        r2 = _extract(sp)
        assert r1 == r2

    def test_exception_in_payload_returns_skip(self):
        # Even with a completely invalid payload type, the extractor never raises.
        from app.services.intelligence.v3.eps_payload_extractor_v1 import (
            extract_fy_eps_observation_from_payload,
        )
        # Pass a payload that will cause getattr issues in internal _extract.
        class BadDict(dict):
            def get(self, key, default=None):
                raise RuntimeError("simulated crash")

        result = extract_fy_eps_observation_from_payload(
            BadDict(), has_source=True
        )
        assert result.skip_reason != ""  # never raises; returns a skip reason

    def test_empty_payload_does_not_raise(self):
        r = _extract({})
        assert r.skip_reason != ""

    def test_none_values_in_payload_do_not_raise(self):
        r = _extract({
            "tag": None, "value": None, "fiscal_period": None,
            "fiscal_year": None, "form": None, "filed": None,
        })
        assert r.skip_reason != ""
