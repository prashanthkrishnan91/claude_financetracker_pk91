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
    assert normalized["sentiment"] == "Mixed"


def test_normalize_reasoning_builds_deterministic_plain_english_when_llm_partial():
    rec = {
        "ticker": "QQQ",
        "action": "BUY",
        "detail": "legacy detail",
        "technical_signal": "BUY",
        "suggested_allocation": 8.5,
        "tax_note": "Tax lot review suggests prioritizing long-term lots first.",
        "reason_tags": [],
    }
    normalized = normalize_reasoning_payload(rec, analyst_verdict={})

    assert normalized["sentiment"] == "Positive"
    assert normalized["plain_language_explanation"]
    assert "What could go right:" in normalized["plain_language_explanation"]
    assert "What could go wrong:" in normalized["plain_language_explanation"]
    assert normalized["key_drivers"]
    assert normalized["main_risks"]
    assert normalized["confidence"] is not None
