"""Phase 14F — PriceBand Visible Context Scaffold v1 tests.

Verifies:
  - Config flag exists and defaults False.
  - Disabled default returns should_render=False and no visible valuation text.
  - Enabled test-only path maps classifiable Phase 14D signals through
    Phase 14E approved visible language.
  - unavailable and negative_eps are not display-eligible.
  - negative_eps never maps to favorable/cheap.
  - unusually_cheap includes quality/risk caution.
  - low confidence cannot produce strong/unqualified visible context.
  - No forbidden keys/phrases leak (target, fair value, intrinsic, buy_below,
    sell_above, threshold, raw metric, internal enum labels).
  - No raw EPS, price, yield, bucket enum, threshold, unavailable reason,
    target price, fair value, intrinsic value, buy_below, sell_above leaks.
  - Output text fields contain no digits representing financial metrics.
  - Output is deterministic.
  - Invalid/unknown signal safely raises ValueError consistent with Phase 14E.
  - Hard lock fields (decision_authority, decision_impact, supporting_context_only,
    no_target_price_emitted, no_fair_value_emitted) are always set correctly.
  - AST static safety: new module does not import forbidden runtime modules.
  - Static safety: runtime paths (snapshot_builder, intel_v3_service,
    decision_policy_v1) do not import the new module.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_MODULE_PATH = (
    Path(__file__).parent.parent
    / "app/services/intelligence/v3/priceband_visible_context_v1.py"
)
_SNAPSHOT_BUILDER_PATH = (
    Path(__file__).parent.parent
    / "app/services/intelligence/v3/snapshot_builder.py"
)
_INTEL_V3_SERVICE_PATH = (
    Path(__file__).parent.parent
    / "app/services/intelligence/v3/intel_v3_service.py"
)
_DECISION_POLICY_PATH = (
    Path(__file__).parent.parent
    / "app/services/intelligence/v3/decision_policy_v1.py"
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_diagnostic(
    *,
    ticker: str = "AAA",
    signal: str = "reasonable",
    confidence: str = "high",
    unavailable_reason: str | None = None,
    priceband_produced: bool = True,
):
    from app.services.intelligence.v3.priceband_shadow_policy_v1 import (
        PriceBandShadowDiagnostic,
        PRICEBAND_SHADOW_POLICY_V1_CONTRACT_VERSION,
        PRICEBAND_POLICY_BASIS,
        PRICEBAND_POLICY_TABLE_ID,
    )
    return PriceBandShadowDiagnostic(
        ticker=ticker,
        priceband_policy_version=PRICEBAND_SHADOW_POLICY_V1_CONTRACT_VERSION,
        safe_for_decision=False,
        shadow_only=True,
        visible_decision_changed=False,
        priceband_produced=priceband_produced,
        valuation_signal=signal,
        valuation_confidence=confidence,
        valuation_basis=PRICEBAND_POLICY_BASIS,
        valuation_policy_table=PRICEBAND_POLICY_TABLE_ID,
        earnings_yield_bucket="four_to_6_percent" if signal == "reasonable" else "",
        sector=None,
        industry=None,
        sector_used_for_classification=False,
        broad_fallback_used=False,
        input_quality="source_linked_fy_eps_and_fresh_price_and_sector",
        plain_english_summary="Valuation looks roughly in line with broad market norms.",
        limitations=["FY-only EPS"],
        unavailable_reason=unavailable_reason,
    )


def _build(*, enabled: bool, diagnostic=None):
    from app.services.intelligence.v3.priceband_visible_context_v1 import (
        build_visible_context,
    )
    if diagnostic is None:
        diagnostic = _make_diagnostic()
    return build_visible_context(enabled=enabled, diagnostic=diagnostic)


# ── Config flag ────────────────────────────────────────────────────────────────

class TestConfigFlag:
    def test_flag_exists_and_default_false(self):
        from app.config import Settings
        s = Settings(
            supabase_url="https://test.supabase.co",
            supabase_anon_key="anon",
            supabase_service_role_key="svc",
            supabase_jwt_secret="jwt",
            encryption_key="a" * 64,
        )
        assert s.intel_v3_priceband_visible_context_v1_enabled is False

    def test_flag_in_model_fields(self):
        from app.config import Settings
        assert "intel_v3_priceband_visible_context_v1_enabled" in Settings.model_fields


# ── Disabled default behavior ──────────────────────────────────────────────────

class TestDisabledDefault:
    def test_should_render_false_when_disabled(self):
        ctx = _build(enabled=False)
        assert ctx.should_render is False

    def test_visible_text_none_when_disabled(self):
        ctx = _build(enabled=False)
        assert ctx.visible_text is None

    def test_confidence_note_none_when_disabled(self):
        ctx = _build(enabled=False)
        assert ctx.confidence_note is None

    def test_blocked_reason_present_when_disabled(self):
        ctx = _build(enabled=False)
        assert ctx.blocked_reason is not None
        assert len(ctx.blocked_reason) > 0

    def test_enabled_field_false_when_disabled(self):
        ctx = _build(enabled=False)
        assert ctx.enabled is False

    def test_disabled_works_for_all_signals(self):
        from app.services.intelligence.v3.priceband_shadow_policy_v1 import (
            _ALL_VALUATION_SIGNALS,
        )
        for signal in _ALL_VALUATION_SIGNALS:
            diag = _make_diagnostic(signal=signal, confidence="high")
            ctx = _build(enabled=False, diagnostic=diag)
            assert ctx.should_render is False, f"should_render True for {signal!r} when disabled"
            assert ctx.visible_text is None


# ── Enabled path: classifiable signals ────────────────────────────────────────

class TestEnabledClassifiableSignals:
    def test_enabled_reasonable_high_confidence_renders(self):
        diag = _make_diagnostic(signal="reasonable", confidence="high")
        ctx = _build(enabled=True, diagnostic=diag)
        assert ctx.should_render is True
        assert ctx.visible_text is not None
        assert len(ctx.visible_text) > 0

    def test_enabled_expensive_renders(self):
        diag = _make_diagnostic(signal="expensive", confidence="high")
        ctx = _build(enabled=True, diagnostic=diag)
        assert ctx.should_render is True
        assert ctx.visible_text is not None

    def test_enabled_elevated_renders(self):
        diag = _make_diagnostic(signal="elevated", confidence="high")
        ctx = _build(enabled=True, diagnostic=diag)
        assert ctx.should_render is True
        assert ctx.visible_text is not None

    def test_enabled_attractive_renders(self):
        diag = _make_diagnostic(signal="attractive", confidence="high")
        ctx = _build(enabled=True, diagnostic=diag)
        assert ctx.should_render is True
        assert ctx.visible_text is not None

    def test_enabled_unusually_cheap_renders(self):
        diag = _make_diagnostic(signal="unusually_cheap", confidence="high")
        ctx = _build(enabled=True, diagnostic=diag)
        assert ctx.should_render is True
        assert ctx.visible_text is not None

    def test_enabled_medium_confidence_renders(self):
        diag = _make_diagnostic(signal="reasonable", confidence="medium")
        ctx = _build(enabled=True, diagnostic=diag)
        assert ctx.should_render is True
        assert ctx.visible_text is not None

    def test_visible_text_from_phase14e_translation(self):
        from app.services.intelligence.v3.priceband_visible_language_v1 import (
            translate_signal_to_visible,
        )
        for signal in ("expensive", "elevated", "reasonable", "attractive", "unusually_cheap"):
            diag = _make_diagnostic(signal=signal, confidence="high")
            ctx = _build(enabled=True, diagnostic=diag)
            expected = translate_signal_to_visible(signal).visible_text
            assert ctx.visible_text == expected, (
                f"Phase 14E translation mismatch for {signal!r}: "
                f"{ctx.visible_text!r} != {expected!r}"
            )


# ── Non-display-eligible signals ──────────────────────────────────────────────

class TestNonDisplayEligible:
    def test_unavailable_not_renderable(self):
        diag = _make_diagnostic(
            signal="unavailable",
            confidence="low",
            priceband_produced=False,
            unavailable_reason="missing_price",
        )
        ctx = _build(enabled=True, diagnostic=diag)
        assert ctx.should_render is False

    def test_unavailable_visible_text_none(self):
        diag = _make_diagnostic(
            signal="unavailable",
            confidence="low",
            priceband_produced=False,
            unavailable_reason="missing_eps",
        )
        ctx = _build(enabled=True, diagnostic=diag)
        assert ctx.visible_text is None

    def test_negative_eps_not_renderable(self):
        diag = _make_diagnostic(signal="negative_eps", confidence="high")
        ctx = _build(enabled=True, diagnostic=diag)
        assert ctx.should_render is False

    def test_negative_eps_visible_text_none(self):
        diag = _make_diagnostic(signal="negative_eps", confidence="high")
        ctx = _build(enabled=True, diagnostic=diag)
        assert ctx.visible_text is None

    def test_negative_eps_never_maps_to_favorable_or_cheap(self):
        diag = _make_diagnostic(signal="negative_eps", confidence="high")
        ctx = _build(enabled=True, diagnostic=diag)
        assert ctx.visible_text is None
        assert ctx.should_render is False
        # Confirm no favorable/cheap language leaks into any field.
        fields = [ctx.blocked_reason, ctx.limitation_text, ctx.source_basis]
        for text in fields:
            if text is None:
                continue
            lower = text.lower()
            assert "cheap" not in lower, f"'cheap' leaked into: {text!r}"
            assert "favorable" not in lower, f"'favorable' leaked into: {text!r}"
            assert "attractive" not in lower, f"'attractive' leaked into: {text!r}"

    def test_low_confidence_not_renderable(self):
        diag = _make_diagnostic(signal="reasonable", confidence="low")
        ctx = _build(enabled=True, diagnostic=diag)
        assert ctx.should_render is False

    def test_low_confidence_visible_text_none(self):
        diag = _make_diagnostic(signal="reasonable", confidence="low")
        ctx = _build(enabled=True, diagnostic=diag)
        assert ctx.visible_text is None

    def test_low_confidence_blocked_reason_present(self):
        diag = _make_diagnostic(signal="attractive", confidence="low")
        ctx = _build(enabled=True, diagnostic=diag)
        assert ctx.blocked_reason is not None
        assert len(ctx.blocked_reason) > 0


# ── unusually_cheap quality/risk caution ──────────────────────────────────────

class TestUnsuallyCheapCaution:
    def test_unusually_cheap_visible_text_includes_quality_risk_caution(self):
        diag = _make_diagnostic(signal="unusually_cheap", confidence="high")
        ctx = _build(enabled=True, diagnostic=diag)
        assert ctx.should_render is True
        assert ctx.visible_text is not None
        text = ctx.visible_text.lower()
        # Phase 14E translation includes "review quality/risk first"
        assert any(
            term in text
            for term in ("quality", "risk", "review", "consider")
        ), f"unusually_cheap visible_text missing quality/risk caution: {ctx.visible_text!r}"

    def test_unusually_cheap_not_a_buy_signal(self):
        diag = _make_diagnostic(signal="unusually_cheap", confidence="high")
        ctx = _build(enabled=True, diagnostic=diag)
        assert ctx.visible_text is not None
        text = ctx.visible_text.lower()
        assert "buy" not in text
        assert "purchase" not in text


# ── Hard lock invariants ───────────────────────────────────────────────────────

class TestHardLockInvariants:
    def test_decision_authority_always_false(self):
        for enabled in (True, False):
            for signal in ("reasonable", "unavailable", "negative_eps"):
                diag = _make_diagnostic(signal=signal, confidence="high")
                ctx = _build(enabled=enabled, diagnostic=diag)
                assert ctx.decision_authority is False, (
                    f"decision_authority True for enabled={enabled}, signal={signal!r}"
                )

    def test_decision_impact_always_none(self):
        for enabled in (True, False):
            for signal in ("attractive", "unavailable", "negative_eps"):
                diag = _make_diagnostic(signal=signal, confidence="high")
                ctx = _build(enabled=enabled, diagnostic=diag)
                assert ctx.decision_impact == "none", (
                    f"decision_impact {ctx.decision_impact!r} for enabled={enabled}"
                )

    def test_supporting_context_only_always_true(self):
        for enabled in (True, False):
            ctx = _build(enabled=enabled)
            assert ctx.supporting_context_only is True

    def test_no_target_price_emitted_always_true(self):
        for enabled in (True, False):
            ctx = _build(enabled=enabled)
            assert ctx.no_target_price_emitted is True

    def test_no_fair_value_emitted_always_true(self):
        for enabled in (True, False):
            ctx = _build(enabled=enabled)
            assert ctx.no_fair_value_emitted is True

    def test_contract_version_correct(self):
        from app.services.intelligence.v3.priceband_visible_context_v1 import (
            VISIBLE_CONTEXT_CONTRACT_VERSION,
        )
        assert VISIBLE_CONTEXT_CONTRACT_VERSION == "phase14f_priceband_visible_context_v1"
        ctx = _build(enabled=False)
        assert ctx.contract_version == "phase14f_priceband_visible_context_v1"

    def test_context_kind_correct(self):
        ctx = _build(enabled=True)
        assert ctx.context_kind == "valuation_context"

    def test_limitation_text_present_always(self):
        for enabled in (True, False):
            ctx = _build(enabled=enabled)
            assert ctx.limitation_text
            assert len(ctx.limitation_text) > 0

    def test_source_basis_present_always(self):
        for enabled in (True, False):
            ctx = _build(enabled=enabled)
            assert ctx.source_basis
            assert len(ctx.source_basis) > 0


# ── Forbidden phrases leakage ──────────────────────────────────────────────────

_FORBIDDEN_PATTERNS = [
    (r"\btarget\s+price\b", "target price"),
    (r"\bfair[_\s]value\b", "fair value"),
    (r"\bintrinsic\b", "intrinsic"),
    (r"\bbuy[_\s]below\b", "buy_below"),
    (r"\bsell[_\s]above\b", "sell_above"),
    (r"\bthreshold\b", "threshold"),
    (r"\bearnings[_\s]yield\b", "earnings_yield"),
    (r"\braw\s+eps\b", "raw EPS"),
    (r"\braw\s+price\b", "raw price"),
    (r"\b\d+\.\d+\s*%", "percentage with decimals"),
    (r"\$\d", "dollar amount"),
    # Raw underscore enum form leakage — plain English equivalents are permitted
    # in approved Phase 14E translations (e.g. "Valuation looks reasonable" is fine)
    (r"\bunusually_cheap\b", "unusually_cheap enum (underscore form)"),
    (r"\bnegative_eps\b", "negative_eps enum (underscore form)"),
    # Bucket enum leakage
    (r"\bfour_to_6_percent\b", "bucket enum"),
    (r"\bsix_to_9_percent\b", "bucket enum"),
    (r"\babove_9_percent\b", "bucket enum"),
    (r"\bzero_to_2_percent\b", "bucket enum"),
    (r"\btwo_to_4_percent\b", "bucket enum"),
    # Technical unavailable reason codes
    (r"\bmissing_eps\b", "unavailable reason code"),
    (r"\bstale_price\b", "unavailable reason code"),
    (r"\bzero_eps_invalid\b", "unavailable reason code"),
    (r"\bnon_positive_price\b", "unavailable reason code"),
    (r"\bmissing_price\b", "unavailable reason code"),
]


def _visible_text_fields(ctx) -> list[str]:
    """Text fields that may be shown to users or influence output."""
    return [
        f for f in [
            ctx.visible_text,
            ctx.confidence_note,
            ctx.limitation_text,
            ctx.blocked_reason,
            ctx.source_basis,
        ]
        if f is not None
    ]


class TestForbiddenPhraseLeakage:
    def test_no_forbidden_phrases_in_any_signal(self):
        from app.services.intelligence.v3.priceband_shadow_policy_v1 import (
            _ALL_VALUATION_SIGNALS,
        )
        for signal in _ALL_VALUATION_SIGNALS:
            diag = _make_diagnostic(signal=signal, confidence="high")
            for enabled in (True, False):
                ctx = _build(enabled=enabled, diagnostic=diag)
                for text in _visible_text_fields(ctx):
                    for pattern, label in _FORBIDDEN_PATTERNS:
                        assert not re.search(pattern, text, re.IGNORECASE), (
                            f"Forbidden '{label}' leaked in "
                            f"signal={signal!r}, enabled={enabled}: {text!r}"
                        )

    def test_no_forbidden_phrases_low_confidence(self):
        for signal in ("reasonable", "attractive"):
            diag = _make_diagnostic(signal=signal, confidence="low")
            ctx = _build(enabled=True, diagnostic=diag)
            for text in _visible_text_fields(ctx):
                for pattern, label in _FORBIDDEN_PATTERNS:
                    assert not re.search(pattern, text, re.IGNORECASE), (
                        f"Forbidden '{label}' leaked in low-conf {signal!r}: {text!r}"
                    )


# ── Digit-free visible output ─────────────────────────────────────────────────

class TestDigitFreeOutput:
    def test_visible_text_contains_no_digits(self):
        for signal in ("expensive", "elevated", "reasonable", "attractive", "unusually_cheap"):
            diag = _make_diagnostic(signal=signal, confidence="high")
            ctx = _build(enabled=True, diagnostic=diag)
            assert ctx.visible_text is not None
            assert not re.search(r"\d", ctx.visible_text), (
                f"Digit in visible_text for {signal!r}: {ctx.visible_text!r}"
            )

    def test_confidence_note_contains_no_digits(self):
        for signal in ("reasonable", "attractive"):
            for conf in ("high", "medium"):
                diag = _make_diagnostic(signal=signal, confidence=conf)
                ctx = _build(enabled=True, diagnostic=diag)
                if ctx.confidence_note is not None:
                    assert not re.search(r"\d", ctx.confidence_note), (
                        f"Digit in confidence_note ({signal!r},{conf!r}): "
                        f"{ctx.confidence_note!r}"
                    )

    def test_limitation_text_contains_no_digits(self):
        ctx = _build(enabled=True)
        assert not re.search(r"\d", ctx.limitation_text), (
            f"Digit in limitation_text: {ctx.limitation_text!r}"
        )

    def test_blocked_reason_contains_no_digits(self):
        for signal in ("unavailable", "negative_eps", "reasonable"):
            for enabled in (True, False):
                confidence = "low" if signal == "reasonable" else "high"
                diag = _make_diagnostic(signal=signal, confidence=confidence)
                ctx = _build(enabled=enabled, diagnostic=diag)
                if ctx.blocked_reason is not None:
                    assert not re.search(r"\d", ctx.blocked_reason), (
                        f"Digit in blocked_reason ({signal!r}): {ctx.blocked_reason!r}"
                    )

    def test_source_basis_contains_no_digits(self):
        ctx = _build(enabled=True)
        assert not re.search(r"\d", ctx.source_basis), (
            f"Digit in source_basis: {ctx.source_basis!r}"
        )


# ── Determinism ────────────────────────────────────────────────────────────────

class TestDeterminism:
    def test_same_signal_same_enabled_yields_same_output(self):
        for signal in ("reasonable", "unusually_cheap", "unavailable", "negative_eps"):
            diag = _make_diagnostic(signal=signal, confidence="high")
            ctx1 = _build(enabled=True, diagnostic=diag)
            ctx2 = _build(enabled=True, diagnostic=diag)
            assert ctx1.should_render == ctx2.should_render
            assert ctx1.visible_text == ctx2.visible_text
            assert ctx1.confidence_note == ctx2.confidence_note
            assert ctx1.blocked_reason == ctx2.blocked_reason

    def test_disabled_deterministic_across_all_signals(self):
        from app.services.intelligence.v3.priceband_shadow_policy_v1 import (
            _ALL_VALUATION_SIGNALS,
        )
        for signal in _ALL_VALUATION_SIGNALS:
            diag = _make_diagnostic(signal=signal, confidence="high")
            ctx1 = _build(enabled=False, diagnostic=diag)
            ctx2 = _build(enabled=False, diagnostic=diag)
            assert ctx1.should_render == ctx2.should_render
            assert ctx1.visible_text == ctx2.visible_text


# ── Invalid / unknown signal ───────────────────────────────────────────────────

class TestInvalidSignal:
    def test_unknown_signal_raises_consistent_with_phase14e(self):
        """Unknown signals should raise ValueError (delegated from Phase 14E translator)."""
        diag = _make_diagnostic(signal="unknown_bogus_signal", confidence="high")
        with pytest.raises(ValueError, match="unknown signal"):
            _build(enabled=True, diagnostic=diag)

    def test_unknown_signal_when_disabled_does_not_raise(self):
        """When disabled, the signal is never translated so no error raised."""
        diag = _make_diagnostic(signal="unknown_bogus_signal", confidence="high")
        ctx = _build(enabled=False, diagnostic=diag)
        assert ctx.should_render is False


# ── AST static safety: new module ─────────────────────────────────────────────

class TestModuleStaticSafety:
    def _src(self) -> str:
        return _MODULE_PATH.read_text()

    def _tree(self):
        return ast.parse(self._src())

    def _imports(self) -> list[str]:
        names = []
        for node in ast.walk(self._tree()):
            if isinstance(node, ast.Import):
                names.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names.append(node.module)
        return names

    def test_no_decision_policy_import(self):
        imports = self._imports()
        assert not any("decision_policy" in m for m in imports), (
            f"Forbidden decision_policy import found: {imports}"
        )

    def test_no_snapshot_builder_import(self):
        imports = self._imports()
        assert not any("snapshot_builder" in m for m in imports), (
            f"Forbidden snapshot_builder import found: {imports}"
        )

    def test_no_intel_v3_service_import(self):
        imports = self._imports()
        assert not any("intel_v3_service" in m for m in imports), (
            f"Forbidden intel_v3_service import found: {imports}"
        )

    def test_no_intel_v3_snapshots_table_reference(self):
        for node in ast.walk(self._tree()):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value != "intel_v3_snapshots", (
                    "Module must not reference intel_v3_snapshots table"
                )

    def test_no_decisioninputv3_reference(self):
        forbidden = {"DecisionInputV3", "PriceBand", "run_v3", "decide"}
        for node in ast.walk(self._tree()):
            if isinstance(node, ast.Name):
                assert node.id not in forbidden, (
                    f"Forbidden name {node.id!r} found in module"
                )
            if isinstance(node, ast.Attribute):
                assert node.attr not in forbidden, (
                    f"Forbidden attribute {node.attr!r} found in module"
                )

    def test_no_provider_or_llm_or_http_imports(self):
        forbidden = {"yfinance", "openai", "anthropic", "httpx", "requests",
                     "urllib", "aiohttp"}
        for m in self._imports():
            top = m.split(".")[0]
            assert top not in forbidden, f"Forbidden provider/LLM/HTTP import: {m}"

    def test_no_db_or_io_imports(self):
        forbidden = {"supabase", "psycopg2", "sqlalchemy", "asyncpg"}
        for m in self._imports():
            top = m.split(".")[0]
            assert top not in forbidden, f"Forbidden DB/IO import: {m}"

    def test_no_db_write_methods(self):
        forbidden = {"insert", "upsert", "update", "delete"}
        for node in ast.walk(self._tree()):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in forbidden, (
                    f"DB write method .{node.func.attr}() found in pure module"
                )

    def test_module_only_imports_priceband_modules(self):
        """Module must only import from the local priceband sibling modules."""
        imports = self._imports()
        local_prefixes = {
            "app.services.intelligence.v3.priceband_shadow_policy_v1",
            "app.services.intelligence.v3.priceband_visible_language_v1",
            "__future__",
            "dataclasses",
        }
        # Accept relative imports too (stored as module name from the from clause)
        for m in imports:
            assert any(
                m.startswith(p) or m in local_prefixes or "priceband" in m
                or m in ("__future__", "dataclasses", "annotations")
                for p in local_prefixes
            ), f"Unexpected import in pure module: {m!r}"


# ── AST static safety: runtime paths do not import the new module ─────────────

class TestRuntimePathsDoNotImportModule:
    """Verify that core runtime paths do not directly import priceband_visible_context_v1.

    Build 3 PR 2B governance note: intel_v3_service.py legitimately references the
    config flag name ``intel_v3_priceband_visible_context_v1_enabled`` (not an import).
    The integration is routed through priceband_snapshot_context_v1 (the dedicated
    integration module), not through direct import of this scaffold module.
    The AST-only check enforces no direct module import; config-attribute references
    are permitted as they are not import statements.
    """
    _MODULE_NAME = "priceband_visible_context_v1"

    def _contains_module_import(self, path: Path) -> bool:
        """Check for direct AST import of priceband_visible_context_v1 only.

        Does NOT do raw-string matching since the config flag name
        ``intel_v3_priceband_visible_context_v1_enabled`` contains the substring
        and is a legitimate non-import reference in intel_v3_service.py.
        """
        src = path.read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and self._MODULE_NAME in node.module:
                    return True
                for alias in node.names:
                    if self._MODULE_NAME in alias.name:
                        return True
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if self._MODULE_NAME in alias.name:
                        return True
        return False

    def test_snapshot_builder_does_not_import_module(self):
        assert not self._contains_module_import(_SNAPSHOT_BUILDER_PATH), (
            "snapshot_builder.py must not import priceband_visible_context_v1"
        )

    def test_intel_v3_service_does_not_directly_import_module(self):
        """intel_v3_service.py must not directly import priceband_visible_context_v1.
        Build 3 PR 2B routes the integration through priceband_snapshot_context_v1.
        """
        assert not self._contains_module_import(_INTEL_V3_SERVICE_PATH), (
            "intel_v3_service.py must not directly import priceband_visible_context_v1 "
            "(use priceband_snapshot_context_v1 as the integration bridge)"
        )

    def test_decision_policy_does_not_import_module(self):
        assert not self._contains_module_import(_DECISION_POLICY_PATH), (
            "decision_policy_v1.py must not import priceband_visible_context_v1"
        )
