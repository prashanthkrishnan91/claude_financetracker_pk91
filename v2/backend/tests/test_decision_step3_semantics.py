from app.services.decision_delta import analyzeDecisionDelta


def _snapshot(total_by_ticker: dict[str, float]):
    return {
        "normalized_tickers": [
            {"ticker": ticker, "amount": amount, "action": "BUY"}
            for ticker, amount in total_by_ticker.items()
        ]
    }


def test_confirmed_execution_matches_recommendation_not_zero():
    snap = _snapshot({"MSFT": 135, "META": 135, "GOOGL": 135, "TSM": 125, "BRK-B": 105})
    actuals = [
        {"ticker": "MSFT", "actual_action": "BOUGHT", "actual_amount": 135},
        {"ticker": "META", "actual_action": "BOUGHT", "actual_amount": 135},
        {"ticker": "GOOGL", "actual_action": "BOUGHT", "actual_amount": 135},
        {"ticker": "TSM", "actual_action": "BOUGHT", "actual_amount": 125},
        {"ticker": "BRK-B", "actual_action": "BOUGHT", "actual_amount": 105},
    ]

    result = analyzeDecisionDelta(snap, actuals)

    assert result["decision_delta"]["total_recommended"] == 635
    assert result["decision_delta"]["total_actual"] == 635
    assert result["status"] == "FULLY_EXECUTED"


def test_zero_actual_only_when_user_explicitly_skips_or_saves_zero():
    snap = _snapshot({"MSFT": 100})
    explicit_zero = [{"ticker": "MSFT", "actual_action": "SKIPPED", "actual_amount": 0}]

    result = analyzeDecisionDelta(snap, explicit_zero)

    assert result["decision_delta"]["total_actual"] == 0
    assert result["status"] == "SKIPPED"
