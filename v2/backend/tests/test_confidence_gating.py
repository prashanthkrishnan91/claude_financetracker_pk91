"""LLM confidence-gating tests — deterministic enforcement of conviction caps.

Validates v3 stability-layer guarantees:
  * Per-ticker ``confidence_cap`` is derived from ``data_completeness_score``
    using the 0.4/0.7 thresholds from the spec.
  * ``_inject_confidence_caps`` mutates the LLM context so the prompt
    contract and the payload stay in lock-step.
  * ``_apply_cap`` clamps absolute conviction deterministically, even when
    the LLM ignores the cap in its output.
"""

from __future__ import annotations


def test_confidence_cap_thresholds():
    from app.services.agents.orchestrator import _confidence_cap_for

    # <0.4 → 0.3
    assert _confidence_cap_for(0.0) == 0.3
    assert _confidence_cap_for(0.39) == 0.3
    # 0.4–0.7 → 0.6
    assert _confidence_cap_for(0.4) == 0.6
    assert _confidence_cap_for(0.55) == 0.6
    assert _confidence_cap_for(0.7) == 0.6
    # >0.7 → 1.0
    assert _confidence_cap_for(0.71) == 1.0
    assert _confidence_cap_for(1.0) == 1.0


def test_confidence_cap_handles_nan_and_invalid():
    from app.services.agents.orchestrator import _confidence_cap_for

    assert _confidence_cap_for(float("nan")) == 0.3
    assert _confidence_cap_for(None) == 0.3  # type: ignore[arg-type]
    assert _confidence_cap_for("oops") == 0.3  # type: ignore[arg-type]


def test_apply_cap_clamps_positive_and_negative():
    from app.services.agents.orchestrator import _apply_cap

    assert _apply_cap(0.8, cap=0.3) == 0.3
    assert _apply_cap(-0.8, cap=0.3) == -0.3
    assert _apply_cap(0.2, cap=0.3) == 0.2
    assert _apply_cap(-0.2, cap=0.3) == -0.2
    # A cap of 0 forces the conviction to 0.
    assert _apply_cap(0.5, cap=0.0) == 0.0
    # None in → None out (preserves "no signal" semantics).
    assert _apply_cap(None, cap=0.6) is None


def test_inject_confidence_caps_populates_portfolio_entries():
    from app.services.agents.orchestrator import _inject_confidence_caps

    ctx = {
        "portfolio": [
            {"ticker": "AAPL", "confidence_score": 0.85,
             "data_quality": {"missing_fields": []}},
            {"ticker": "MEME", "confidence_score": 0.2,
             "data_quality": {"missing_fields": ["sentiment", "news"]}},
            {"ticker": "MID", "confidence_score": 0.55,
             "data_quality": {"missing_fields": ["technical"]}},
        ],
        "data_quality": {"missing_fields": []},
    }
    _inject_confidence_caps(ctx)

    by_t = {p["ticker"]: p for p in ctx["portfolio"]}
    assert by_t["AAPL"]["confidence_cap"] == 1.0
    assert by_t["AAPL"]["data_completeness_score"] == 0.85
    assert by_t["MEME"]["confidence_cap"] == 0.3
    assert by_t["MEME"]["missing_fields"] == ["sentiment", "news"]
    assert by_t["MID"]["confidence_cap"] == 0.6


def test_extract_confidence_from_context_prefers_completeness_field():
    from app.services.agents.orchestrator import _extract_confidence_from_context

    ctx = {
        "portfolio": [
            {"ticker": "AAPL", "confidence_score": 0.4, "data_completeness_score": 0.9},
            {"ticker": "TSLA", "confidence_score": 0.2},
            {"ticker": "", "confidence_score": 0.5},  # skipped — no ticker
        ]
    }
    out = _extract_confidence_from_context(ctx)
    assert out == {"AAPL": 0.9, "TSLA": 0.2}
