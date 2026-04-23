from app.services.reasoning_contract import normalize_reasoning_payload


def test_normalize_reasoning_uses_analyst_narrative_without_sentiment():
    rec = {
        "ticker": "NVDA",
        "action": "HOLD",
        "detail": "legacy detail",
        "investment_thesis": "legacy thesis",
        "reason_tags": ["low_data"],
    }
    analyst_verdict = {
        "action": "HOLD",
        "conviction": 0.42,
        "confidence": 0.74,
        "reasoning": "Revenue growth is slowing while valuation remains high, so upside may be limited.",
        "summary": "Upside appears limited near-term.",
        "key_drivers": ["growth deceleration"],
        "risks": ["multiple compression"],
        # sentiment intentionally missing
    }

    normalized = normalize_reasoning_payload(rec, analyst_verdict=analyst_verdict)
    assert normalized["thesis"].startswith("Revenue growth is slowing")
    assert normalized["plain_language_explanation"].startswith("Revenue growth is slowing")
    assert normalized["summary"] == "Upside appears limited near-term."
    assert normalized["sentiment"] == "Unavailable"
