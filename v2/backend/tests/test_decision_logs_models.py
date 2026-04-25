from app.models.decision import DecisionLogCreateRequest, DecisionLogUpdateRequest


def test_decision_log_create_defaults():
    payload = DecisionLogCreateRequest(recommendation_snapshot={"foo": "bar"})
    assert payload.source == "deploy"
    assert payload.status == "draft"
    assert payload.actual_decisions == []


def test_decision_log_update_status_validation():
    payload = DecisionLogUpdateRequest(status="executed", notes="done")
    assert payload.status == "executed"
    assert payload.notes == "done"
