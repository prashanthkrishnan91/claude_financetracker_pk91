from app.services.decision_log_service import DecisionLogService


class _FakeExecResult:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, row):
        self._row = row
        self._payload = {}

    def update(self, payload):
        self._payload = payload
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def execute(self):
        merged = {**self._row, **self._payload}
        return _FakeExecResult([merged])


class _FakeClient:
    def __init__(self, row):
        self._row = row

    def table(self, _name):
        return _FakeTable(self._row)


def test_evaluate_decision_performance_replaced_and_skipped():
    row = {
        "id": "log-1",
        "user_id": "u1",
        "recommendation_snapshot": {
            "normalized_tickers": [
                {"ticker": "MSFT", "amount": 1000},
                {"ticker": "TSM", "amount": 1000},
            ]
        },
        "price_snapshot": {
            "MSFT": {"price": 100.0, "timestamp": "2026-01-01T00:00:00+00:00"},
            "TSM": {"price": 100.0, "timestamp": "2026-01-01T00:00:00+00:00"},
            "AAPL": {"price": 100.0, "timestamp": "2026-01-01T00:00:00+00:00"},
        },
        "actual_decisions": [
            {"ticker": "MSFT", "actual_action": "BOUGHT", "actual_amount": 1000},
            {"ticker": "TSM", "actual_action": "REPLACED", "actual_amount": 1000, "replacement_ticker": "AAPL"},
        ],
    }

    svc = object.__new__(DecisionLogService)
    svc.client = _FakeClient(row)
    svc.get = lambda user_id, decision_log_id, evaluate_if_missing=False: row
    async def _fake_prices(_tickers):
        return {
            "MSFT": {"price": 110.0, "timestamp": "2026-02-01T00:00:00+00:00"},
            "TSM": {"price": 90.0, "timestamp": "2026-02-01T00:00:00+00:00"},
            "AAPL": {"price": 120.0, "timestamp": "2026-02-01T00:00:00+00:00"},
        }

    svc._fetch_price_map_async = _fake_prices  # type: ignore[method-assign]

    updated = svc.evaluateDecisionPerformance(user_id="u1", decision_log_id="log-1")

    assert updated is not None
    snap = updated["performance_snapshot"]
    assert snap["status"] == "ready"
    assert round(snap["portfolio"]["recommended_return"], 2) == 0.0
    assert round(snap["portfolio"]["actual_return"], 2) == 15.0
    assert round(snap["portfolio"]["delta"], 2) == 15.0
    assert snap["portfolio"]["matched_model"] is False
    per_ticker = {item["ticker"]: item for item in snap["per_ticker"]}
    assert round(per_ticker["MSFT"]["delta_pct"], 2) == 0.0
    assert round(per_ticker["TSM → AAPL"]["delta_pct"], 2) == 30.0


def test_evaluate_decision_performance_backfills_old_logs_and_includes_all_recommendations():
    row = {
        "id": "log-2",
        "user_id": "u1",
        "recommendation_snapshot": {
            "normalized_tickers": [
                {"ticker": "TSM", "amount": 1000},
                {"ticker": "MSFT", "amount": 1000},
                {"ticker": "META", "amount": 1000},
                {"ticker": "GOOGL", "amount": 1000},
                {"ticker": "NVDA", "amount": 1000},
            ]
        },
        "price_snapshot": {},
        "actual_decisions": [
            {"ticker": "MSFT", "actual_action": "REPLACED", "actual_amount": 1000, "replacement_ticker": "VYM"},
        ],
    }

    svc = object.__new__(DecisionLogService)
    svc.client = _FakeClient(row)
    svc.get = lambda user_id, decision_log_id, evaluate_if_missing=False: row

    async def _fake_prices(_tickers):
        return {ticker: {"price": 100.0, "timestamp": "2026-02-01T00:00:00+00:00"} for ticker in _tickers}

    svc._fetch_price_map_async = _fake_prices  # type: ignore[method-assign]
    updated = svc.evaluateDecisionPerformance(user_id="u1", decision_log_id="log-2")

    assert updated is not None
    snapshot = updated["price_snapshot"]
    assert snapshot["_meta"]["backfilled"] is True
    assert "backfilled_at" in snapshot["_meta"]

    perf = updated["performance_snapshot"]
    assert perf["status"] == "baseline_captured"
    assert perf["portfolio"]["too_early_to_judge"] is True
    assert perf["portfolio"]["matched_model"] is False
    assert perf["portfolio"]["summary_text"] == "Performance baseline captured. Return comparison will become meaningful after prices move."
    tickers = [row["recommended_ticker"] for row in perf["per_ticker"]]
    assert tickers == ["TSM", "MSFT", "META", "GOOGL", "NVDA"]
    assert any(row["ticker"] == "MSFT → VYM" for row in perf["per_ticker"])


def test_evaluate_decision_performance_marks_missing_prices_without_silent_zeroing():
    row = {
        "id": "log-3",
        "user_id": "u1",
        "recommendation_snapshot": {"normalized_tickers": [{"ticker": "NVDA", "amount": 1000}]},
        "price_snapshot": {"NVDA": {"price": 100.0, "timestamp": "2026-01-01T00:00:00+00:00"}},
        "actual_decisions": [{"ticker": "NVDA", "actual_action": "BOUGHT", "actual_amount": 1000}],
    }

    svc = object.__new__(DecisionLogService)
    svc.client = _FakeClient(row)
    svc.get = lambda user_id, decision_log_id, evaluate_if_missing=False: row

    async def _fake_prices(_tickers):
        return {}

    svc._fetch_price_map_async = _fake_prices  # type: ignore[method-assign]
    updated = svc.evaluateDecisionPerformance(user_id="u1", decision_log_id="log-3")

    assert updated is not None
    perf = updated["performance_snapshot"]
    assert perf["per_ticker"][0]["status"] == "missing_price"
    assert perf["per_ticker"][0]["reason"] == "Missing entry price/current price"
    assert perf["per_ticker"][0]["recommended_return_pct"] is None
    assert perf["per_ticker"][0]["actual_return_pct"] is None
    assert perf["per_ticker"][0]["delta_pct"] is None
    assert perf["data_quality"][0]["ticker"] == "NVDA"
    assert perf["status"] == "missing_price"


def test_evaluate_decision_performance_zero_delta_is_baseline_not_underperformance():
    row = {
        "id": "log-4",
        "user_id": "u1",
        "recommendation_snapshot": {"normalized_tickers": [{"ticker": "MSFT", "amount": 1000}]},
        "price_snapshot": {
            "MSFT": {"price": 100.0, "timestamp": "2026-01-01T00:00:00+00:00"},
            "_meta": {"baseline_captured_at": "2026-01-01T00:00:00+00:00", "backfilled": False},
        },
        "actual_decisions": [{"ticker": "MSFT", "actual_action": "BOUGHT", "actual_amount": 1000}],
    }

    svc = object.__new__(DecisionLogService)
    svc.client = _FakeClient(row)
    svc.get = lambda user_id, decision_log_id, evaluate_if_missing=False: row

    async def _fake_prices(_tickers):
        return {"MSFT": {"price": 100.0, "timestamp": "2026-02-01T00:00:00+00:00"}}

    svc._fetch_price_map_async = _fake_prices  # type: ignore[method-assign]
    updated = svc.evaluateDecisionPerformance(user_id="u1", decision_log_id="log-4")

    assert updated is not None
    perf = updated["performance_snapshot"]
    assert perf["status"] == "baseline_captured"
    assert perf["portfolio"]["summary_text"] == "Performance baseline captured. Return comparison will become meaningful after prices move."
