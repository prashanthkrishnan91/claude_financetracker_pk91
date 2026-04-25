from app.services.decision_delta import analyzeDecisionDelta


def test_analyze_decision_delta_fully_executed():
    result = analyzeDecisionDelta(
        recommendation_snapshot={
            "normalized_tickers": [
                {"ticker": "MSFT", "amount": 300},
                {"ticker": "AAPL", "amount": 200},
            ]
        },
        actual_decisions=[
            {"ticker": "MSFT", "actual_action": "BOUGHT", "actual_amount": 300},
            {"ticker": "AAPL", "actual_action": "BOUGHT", "actual_amount": 250},
        ],
    )
    assert result["status"] == "FULLY_EXECUTED"
    assert result["decision_delta"]["total_recommended"] == 500
    assert result["decision_delta"]["total_actual"] == 550


def test_analyze_decision_delta_skipped_and_replaced():
    result = analyzeDecisionDelta(
        recommendation_snapshot={
            "normalized_tickers": [
                {"ticker": "MSFT", "amount": 600, "rationale": "growth exposure"},
                {"ticker": "AAPL", "amount": 300},
            ]
        },
        actual_decisions=[
            {
                "ticker": "MSFT",
                "actual_action": "REPLACED",
                "actual_amount": 500,
                "replacement_ticker": "VYM",
                "reason": "higher dividend income",
            },
            {"ticker": "AAPL", "actual_action": "SKIPPED", "actual_amount": 0},
        ],
    )
    assert result["status"] == "PARTIALLY_EXECUTED"
    assert result["decision_delta"]["skipped_tickers"] == ["AAPL"]
    assert result["decision_delta"]["category_shift"]["single_to_etf"] is True
    assert result["risk_behavior"] == "more_conservative"
