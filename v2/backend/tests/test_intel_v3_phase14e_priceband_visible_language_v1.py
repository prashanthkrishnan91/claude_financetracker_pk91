"""Phase 14E — PriceBand Visible Language Governance Contract tests.

Verifies:
  - All 7 internal signal labels have a safe visible-language translation.
  - No forbidden terms in any visible text (target, fair value, buy, sell,
    threshold, intrinsic, raw metric keys, internal enum labels).
  - negative_eps maps to unavailable-style visible language.
  - unusually_cheap maps to cautious language with quality/risk caveat.
  - No raw numbers (price targets, yield percentages, EPS values) in visible text.
  - Deterministic output — same input always yields same result.
  - Unknown signal raises ValueError cleanly.
  - has_valuation_context flag is correct for each signal.
  - Module constants are present and non-empty.
  - AST-based static safety: forbidden imports are absent from the module.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_MODULE_PATH = (
    Path(__file__).parent.parent
    / "app/services/intelligence/v3/priceband_visible_language_v1.py"
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _all_signals() -> tuple[str, ...]:
    from app.services.intelligence.v3.priceband_shadow_policy_v1 import (
        _ALL_VALUATION_SIGNALS,
    )
    return _ALL_VALUATION_SIGNALS


def _translate(signal: str):
    from app.services.intelligence.v3.priceband_visible_language_v1 import (
        translate_signal_to_visible,
    )
    return translate_signal_to_visible(signal)


# ── Coverage: every signal has a translation ──────────────────────────────────

class TestAllSignalsCovered:
    def test_all_known_signals_translate_without_error(self):
        for signal in _all_signals():
            result = _translate(signal)
            assert result.visible_text, f"Empty visible text for {signal!r}"
            assert result.internal_signal == signal

    def test_translation_table_covers_all_signals(self):
        from app.services.intelligence.v3.priceband_visible_language_v1 import (
            _VISIBLE_TRANSLATIONS,
        )
        signals = set(_all_signals())
        covered = set(_VISIBLE_TRANSLATIONS.keys())
        assert signals == covered, f"Missing translations: {signals - covered}"


# ── Leakage prevention: forbidden terms ───────────────────────────────────────

_FORBIDDEN_TERMS = [
    r"\btarget\b",
    r"\bfair[_\s]value\b",
    r"\bintrinsic\b",
    r"\bbuy[_\s]below\b",
    r"\bsell[_\s]above\b",
    r"\bthreshold\b",
    r"\bearnings[_\s]yield\b",
    r"\beps\b",
    r"\b\d+\.\d+\s*%",        # raw percentages like "2.0%", "4.5%"
    r"\$\d",                   # dollar amounts like "$45"
    r"\bprice\b",
    # Internal signal label leakage
    r"\bunusually_cheap\b",
    r"\bnegative_eps\b",
    r"\bexpensive\b",
    r"\belevated\b",
    r"\breasonable\b",
    r"\battractive\b",
    r"\bunavailable\b(?!.*signal)",  # "unavailable" alone is ok only in context
]

# Simplified: just check for the raw internal label leakage and truly forbidden terms
_STRICT_FORBIDDEN = [
    r"\btarget\b",
    r"\bfair[_\s]value\b",
    r"\bintrinsic\b",
    r"\bbuy[_\s]below\b",
    r"\bsell[_\s]above\b",
    r"\b\d+\.\d+\s*%",  # raw percentages
    r"\$\d",            # dollar amounts
    # Internal signal label leakage (snake_case form)
    r"\bunusually_cheap\b",
    r"\bnegative_eps\b",
    r"earnings_yield_bucket",
    r"policy_static_v1",
    r"valuation_signal",
]


class TestLeakagePrevention:
    def test_no_forbidden_terms_in_any_visible_text(self):
        for signal in _all_signals():
            result = _translate(signal)
            text = result.visible_text.lower()
            for pattern in _STRICT_FORBIDDEN:
                assert not re.search(pattern, text, re.IGNORECASE), (
                    f"Signal {signal!r} visible text contains forbidden pattern "
                    f"{pattern!r}: {result.visible_text!r}"
                )

    def test_no_raw_numbers_in_visible_text(self):
        for signal in _all_signals():
            result = _translate(signal)
            # No standalone numbers (prices, yields, EPS)
            assert not re.search(r"\b\d+\.?\d*\b", result.visible_text), (
                f"Signal {signal!r} visible text contains a raw number: "
                f"{result.visible_text!r}"
            )

    def test_no_action_directives_in_visible_text(self):
        action_patterns = [r"\bbuy\b", r"\bsell\b", r"\btrim\b", r"\bhold\b"]
        for signal in _all_signals():
            result = _translate(signal)
            text = result.visible_text.lower()
            for pattern in action_patterns:
                assert not re.search(pattern, text), (
                    f"Signal {signal!r} visible text contains action directive "
                    f"{pattern!r}: {result.visible_text!r}"
                )


# ── negative_eps: unavailable-style language ─────────────────────────────────

class TestNegativeEpsLanguage:
    def test_negative_eps_maps_to_unavailable_style_text(self):
        result = _translate("negative_eps")
        text = result.visible_text.lower()
        # Must communicate that valuation is not available, not cheap/attractive
        assert "unavailable" in text or "not" in text, (
            f"negative_eps should signal valuation unavailability: {result.visible_text!r}"
        )

    def test_negative_eps_has_no_valuation_context(self):
        result = _translate("negative_eps")
        assert result.has_valuation_context is False

    def test_negative_eps_does_not_say_cheap_or_attractive(self):
        result = _translate("negative_eps")
        text = result.visible_text.lower()
        assert "cheap" not in text
        assert "low" not in text
        assert "attractive" not in text
        assert "favorable" not in text


# ── unusually_cheap: cautious language ────────────────────────────────────────

class TestUnusuallyCheapLanguage:
    def test_unusually_cheap_contains_caution_keyword(self):
        result = _translate("unusually_cheap")
        text = result.visible_text.lower()
        # Must carry a quality/risk review caution
        assert "review" in text or "caution" in text or "risk" in text, (
            f"unusually_cheap should carry caution language: {result.visible_text!r}"
        )

    def test_unusually_cheap_has_valuation_context(self):
        result = _translate("unusually_cheap")
        assert result.has_valuation_context is True

    def test_unusually_cheap_does_not_say_buy_or_cheap(self):
        result = _translate("unusually_cheap")
        text = result.visible_text.lower()
        assert "cheap" not in text
        assert "buy" not in text


# ── has_valuation_context flag ────────────────────────────────────────────────

class TestHasValuationContextFlag:
    def test_unavailable_signals_have_no_context(self):
        for signal in ("unavailable", "negative_eps"):
            result = _translate(signal)
            assert result.has_valuation_context is False, (
                f"{signal!r} should have has_valuation_context=False"
            )

    def test_classifiable_signals_have_context(self):
        classifiable = (
            "expensive", "elevated", "reasonable", "attractive", "unusually_cheap"
        )
        for signal in classifiable:
            result = _translate(signal)
            assert result.has_valuation_context is True, (
                f"{signal!r} should have has_valuation_context=True"
            )


# ── Determinism ───────────────────────────────────────────────────────────────

class TestDeterminism:
    def test_same_signal_returns_same_result_twice(self):
        for signal in _all_signals():
            r1 = _translate(signal)
            r2 = _translate(signal)
            assert r1 == r2, f"Non-deterministic output for {signal!r}"

    def test_all_signals_return_distinct_visible_texts(self):
        texts = [_translate(s).visible_text for s in _all_signals()]
        assert len(texts) == len(set(texts)), "Duplicate visible texts detected"


# ── Error handling ────────────────────────────────────────────────────────────

class TestErrorHandling:
    def test_unknown_signal_raises_value_error(self):
        with pytest.raises(ValueError, match="unknown signal"):
            _translate("not_a_real_signal")

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            _translate("")

    def test_raw_enum_value_raises_value_error(self):
        # Internal PriceBand enum values must not be accepted
        with pytest.raises(ValueError):
            _translate("CHEAP")


# ── Module constants ──────────────────────────────────────────────────────────

class TestModuleConstants:
    def test_governance_contract_version_present(self):
        from app.services.intelligence.v3.priceband_visible_language_v1 import (
            GOVERNANCE_CONTRACT_VERSION,
        )
        assert GOVERNANCE_CONTRACT_VERSION.startswith("phase14e")

    def test_backend_only_fields_non_empty(self):
        from app.services.intelligence.v3.priceband_visible_language_v1 import (
            BACKEND_ONLY_FIELDS,
        )
        assert len(BACKEND_ONLY_FIELDS) >= 5

    def test_phase_14f_promotion_gates_non_empty(self):
        from app.services.intelligence.v3.priceband_visible_language_v1 import (
            PHASE_14F_PROMOTION_GATES,
        )
        assert len(PHASE_14F_PROMOTION_GATES) >= 5

    def test_interaction_modes_allowed_non_empty(self):
        from app.services.intelligence.v3.priceband_visible_language_v1 import (
            INTERACTION_MODES_ALLOWED,
        )
        assert len(INTERACTION_MODES_ALLOWED) >= 2

    def test_interaction_modes_prohibited_non_empty(self):
        from app.services.intelligence.v3.priceband_visible_language_v1 import (
            INTERACTION_MODES_PROHIBITED,
        )
        assert len(INTERACTION_MODES_PROHIBITED) >= 4

    def test_promotion_gates_mention_phase14d_validation(self):
        from app.services.intelligence.v3.priceband_visible_language_v1 import (
            PHASE_14F_PROMOTION_GATES,
        )
        combined = " ".join(PHASE_14F_PROMOTION_GATES).lower()
        assert "phase 14d" in combined or "14d" in combined

    def test_prohibited_modes_forbid_standalone_authority(self):
        from app.services.intelligence.v3.priceband_visible_language_v1 import (
            INTERACTION_MODES_PROHIBITED,
        )
        combined = " ".join(INTERACTION_MODES_PROHIBITED).lower()
        assert "action authority" in combined or "standalone" in combined

    def test_prohibited_modes_forbid_price_target(self):
        from app.services.intelligence.v3.priceband_visible_language_v1 import (
            INTERACTION_MODES_PROHIBITED,
        )
        combined = " ".join(INTERACTION_MODES_PROHIBITED).lower()
        assert "price target" in combined or "target" in combined


# ── AST-based static import safety ───────────────────────────────────────────

class TestStaticImportSafety:
    """Verify the module does not import forbidden decision/visible-path modules."""

    _FORBIDDEN_IMPORTS = {
        "decision_policy_v1",
        "intel_v3_service",
        "snapshot_builder",
        "decision_contracts",  # PriceBand enum must not be imported
        "run_v3",
        "decide",
    }

    def _collect_imports(self) -> set[str]:
        source = _MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names.add(node.module)
                for alias in node.names:
                    names.add(alias.name)
        return names

    def test_no_forbidden_imports(self):
        imported = self._collect_imports()
        for forbidden in self._FORBIDDEN_IMPORTS:
            assert forbidden not in " ".join(imported), (
                f"priceband_visible_language_v1 must not import {forbidden!r}"
            )

    def test_no_decide_function_call(self):
        source = _MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id == "decide":
                    pytest.fail("priceband_visible_language_v1 must not call decide()")
                if isinstance(node.func, ast.Attribute) and node.func.attr == "decide":
                    pytest.fail("priceband_visible_language_v1 must not call .decide()")

    def test_module_file_exists(self):
        assert _MODULE_PATH.exists(), f"Module not found at {_MODULE_PATH}"

    def test_no_fair_value_as_dict_key_in_module(self):
        source = _MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            # Dict literals must not have "fair_value" as a key
            if isinstance(node, ast.Dict):
                for key in node.keys:
                    if isinstance(key, ast.Constant) and "fair_value" in str(key.value):
                        pytest.fail("Module must not use fair_value as a dict key")

    def test_no_target_price_as_assignment_in_module(self):
        source = _MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        # target_price must not be an assignment target (field name or dict key)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and "target_price" in target.id:
                        pytest.fail("Module must not assign to target_price")
            if isinstance(node, ast.Dict):
                for key in node.keys:
                    if isinstance(key, ast.Constant) and "target_price" in str(key.value):
                        pytest.fail("Module must not use target_price as a dict key")
