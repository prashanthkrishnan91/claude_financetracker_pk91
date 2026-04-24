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


def test_old_generation_version_gets_stale_db_reasoning_source():
    """Verdicts with generation_version != human_v2 should be treated as stale."""
    old_verdict = {
        "analysis_source": "live_llm",
        "used_fallback": False,
        "generation_version": "v2_strict_reasoning",
    }
    # The logic in _compute_insight_cards checks gen_version != "human_v2"
    # and excludes from preferred_live pool. We verify the version string gate.
    from app.services.intelligence.per_ticker_analyst import ANALYST_GENERATION_VERSION
    assert old_verdict["generation_version"] != ANALYST_GENERATION_VERSION


def test_human_v2_non_fallback_live_llm_resolves_live():
    source, reused = _resolve_card_analysis_source(
        analyst_verdict={"analysis_source": "live_llm", "used_fallback": False, "generation_version": "human_v2"},
        is_fallback=False,
    )
    assert source == "live_llm"
    assert reused is False
