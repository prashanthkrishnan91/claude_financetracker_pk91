from app.services.recommendation_engine import _resolve_card_analysis_source


def test_fallback_card_never_counts_as_cached_reuse():
    source, reused = _resolve_card_analysis_source(
        analyst_verdict={"analysis_source": "cached_run", "used_fallback": True},
        is_fallback=True,
    )
    assert source == "deterministic_fallback"
    assert reused is False


def test_nonfallback_cached_card_can_be_reused():
    source, reused = _resolve_card_analysis_source(
        analyst_verdict={"analysis_source": "cached_run", "used_fallback": False},
        is_fallback=False,
    )
    assert source == "cached_run"
    assert reused is True


def test_nonfallback_live_card_defaults_to_live_llm():
    source, reused = _resolve_card_analysis_source(
        analyst_verdict={"analysis_source": "live_llm"},
        is_fallback=False,
    )
    assert source == "live_llm"
    assert reused is False
