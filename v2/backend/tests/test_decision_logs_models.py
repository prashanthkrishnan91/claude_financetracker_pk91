from app.models.decision import DecisionLogCreateRequest, DecisionLogUpdateRequest


def test_decision_log_create_defaults():
    payload = DecisionLogCreateRequest(recommendation_snapshot={"foo": "bar"})
    assert payload.source == "deploy"
    assert payload.actual_decisions == []


def test_decision_log_update_notes_only():
    payload = DecisionLogUpdateRequest(notes="done")
    assert payload.notes == "done"
