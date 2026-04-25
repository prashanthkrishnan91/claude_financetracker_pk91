from app.services.decision_log_service import DecisionLogService


def _make_service_with_rows(rows):
    svc = object.__new__(DecisionLogService)
    svc.list = lambda user_id, limit=10: rows[:limit]
    return svc


def test_behavior_profile_uses_recency_weighted_median_and_confidence_high():
    rows = [
        {"decision_delta": {"total_recommended": 100, "total_actual": 70}, "execution_gap_percent": 30, "actual_decisions": []},  # newest
        {"decision_delta": {"total_recommended": 100, "total_actual": 75}, "execution_gap_percent": 25, "actual_decisions": []},
        {"decision_delta": {"total_recommended": 100, "total_actual": 500}, "execution_gap_percent": -400, "actual_decisions": []},  # outlier
        {"decision_delta": {"total_recommended": 100, "total_actual": 80}, "execution_gap_percent": 20, "actual_decisions": []},
        {"decision_delta": {"total_recommended": 100, "total_actual": 82}, "execution_gap_percent": 18, "actual_decisions": []},
        {"decision_delta": {"total_recommended": 100, "total_actual": 85}, "execution_gap_percent": 15, "actual_decisions": []},
    ]
    svc = _make_service_with_rows(rows)

    profile = svc.getUserBehaviorProfile("u", limit=10)

    assert profile["sample_size"] == 6
    assert profile["personalization_confidence"] == "High"
    # Weighted median should stay in realistic range despite one extreme outlier.
    assert 0.7 <= profile["stable_deploy_ratio"] <= 0.9


def test_behavior_profile_confidence_gating_by_sample_size():
    low_rows = [
        {"decision_delta": {"total_recommended": 100, "total_actual": 80}, "execution_gap_percent": 20, "actual_decisions": []},
        {"decision_delta": {"total_recommended": 100, "total_actual": 82}, "execution_gap_percent": 18, "actual_decisions": []},
    ]
    medium_rows = low_rows + [
        {"decision_delta": {"total_recommended": 100, "total_actual": 79}, "execution_gap_percent": 21, "actual_decisions": []},
        {"decision_delta": {"total_recommended": 100, "total_actual": 81}, "execution_gap_percent": 19, "actual_decisions": []},
    ]

    low = _make_service_with_rows(low_rows).getUserBehaviorProfile("u", limit=10)
    medium = _make_service_with_rows(medium_rows).getUserBehaviorProfile("u", limit=10)

    assert low["personalization_confidence"] == "Low"
    assert low["adjustment_strength"] == 0.0
    assert medium["personalization_confidence"] == "Medium"
    assert medium["adjustment_strength"] == 0.5
