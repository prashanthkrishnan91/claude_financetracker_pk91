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
    assert round(snap["portfolio"]["recommended_return"], 2) == 0.0
    assert round(snap["portfolio"]["actual_return"], 2) == 15.0
    assert round(snap["portfolio"]["delta"], 2) == 15.0
    per_ticker = {item["ticker"]: item for item in snap["per_ticker"]}
    assert round(per_ticker["MSFT"]["delta_pct"], 2) == 0.0
    assert round(per_ticker["TSM"]["delta_pct"], 2) == 30.0
