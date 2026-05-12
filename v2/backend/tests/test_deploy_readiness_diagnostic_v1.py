"""Tests — Deploy v3 production readiness diagnostic v1 (Stage 2.5D).

Acceptance gates proven:
  A. No snapshot → all gates False, next_action = create snapshot.
  B. Stale snapshot → sizing_values_ready False, next_action = refresh snapshot.
  C. Legacy snapshot (no market_value_usd) → fresh but market values missing.
  D. Enriched snapshot, missing target allocations → target_allocation_ready False.
  E. Target total under 98% → TARGET_ALLOCATION_TOTAL_UNDERALLOCATED suppressed.
  F. Target total over 102% → TARGET_ALLOCATION_TOTAL_OVERALLOCATED suppressed.
  G. Invalid/missing policy config → policy_ready False.
  H. All gates ready → exact_dollar_ready True, next_action = ready.
  I. Policy section does not expose env secret values (minimum_trade_usd value absent).
  J. Conflicting target allocations → conflicting_tickers populated, readiness False.
  K. Diagnostic does not call providers, LLM, broker, or legacy allocation engine.
  L. Snapshot metadata (id, timestamp, age, status) is included in response.
"""
from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from app.services.deploy.deploy_readiness_diagnostic_v1 import (
    build_readiness_diagnostic,
    _next_required_action,
)
from app.services.deploy.deploy_sizing_source_adapter_v1 import STALE_THRESHOLD_HOURS

# ── Fixtures and helpers ──────────────────────────────────────────────────────

_UID = UUID("00000000-0000-0000-0000-000000000099")

_CERTIFIED_POLICY = {"minimum_trade_usd": 1.0, "rounding_policy": "WHOLE_DOLLAR"}


def _run(coro) -> object:
    return asyncio.run(coro)


def _fresh_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stale_iso() -> str:
    ts = datetime.now(timezone.utc) - timedelta(hours=STALE_THRESHOLD_HOURS + 2)
    return ts.isoformat()


def _make_db(
    snap_rows: list | None = None,
    target_rows: list | None = None,
    ps_raise: Exception | None = None,
    ta_raise: Exception | None = None,
) -> MagicMock:
    """Build a mock DB client for diagnostic tests."""
    db = MagicMock()

    ps_chain = MagicMock()
    ps_chain.select.return_value = ps_chain
    ps_chain.eq.return_value = ps_chain
    ps_chain.order.return_value = ps_chain
    ps_chain.limit.return_value = ps_chain
    if ps_raise is not None:
        ps_chain.execute.side_effect = ps_raise
    else:
        ps_res = MagicMock()
        ps_res.data = snap_rows if snap_rows is not None else []
        ps_chain.execute.return_value = ps_res

    ta_chain = MagicMock()
    ta_chain.select.return_value = ta_chain
    ta_chain.eq.return_value = ta_chain
    if ta_raise is not None:
        ta_chain.execute.side_effect = ta_raise
    else:
        ta_res = MagicMock()
        ta_res.data = target_rows if target_rows is not None else []
        ta_chain.execute.return_value = ta_res

    def _table(name: str) -> MagicMock:
        if name == "portfolio_snapshots":
            return ps_chain
        if name == "target_allocations":
            return ta_chain
        return MagicMock()

    db.table.side_effect = _table
    return db


def _enriched_snapshot(
    snap_id: str = "snap-001",
    snapshot_at: str | None = None,
    total_equity: float = 100_000.0,
    cash_balance: float = 5_000.0,
    positions: list | None = None,
) -> dict:
    """Build a portfolio_snapshots row with market_value_usd populated."""
    if snapshot_at is None:
        snapshot_at = _fresh_iso()
    if positions is None:
        positions = [
            {
                "ticker": "AAPL",
                "market_value_usd": 95_000.0,
                "shares": 500,
                "avg_cost": 150.0,
            }
        ]
    return {
        "id": snap_id,
        "snapshot_at": snapshot_at,
        "total_equity": total_equity,
        "cash_balance": cash_balance,
        "positions_data": positions,
    }


def _target_row(ticker: str, target_pct: float) -> dict:
    return {"ticker": ticker, "target_pct": target_pct}


# ── Gate A: no snapshot ───────────────────────────────────────────────────────


def test_no_snapshot_all_gates_false():
    db = _make_db(snap_rows=[])
    result = _run(build_readiness_diagnostic(_UID, db_client=db, _policy_config=None))

    assert result["exact_dollar_ready"] is False
    assert result["sizing_values_ready"] is False
    assert result["target_allocation_ready"] is False
    assert result["policy_ready"] is False


def test_no_snapshot_status_missing():
    db = _make_db(snap_rows=[])
    result = _run(build_readiness_diagnostic(_UID, db_client=db, _policy_config=None))

    assert result["snapshot"]["present"] is False
    assert result["snapshot"]["status"] == "missing"
    assert result["snapshot"]["snapshot_id"] is None


def test_no_snapshot_next_action_create_snapshot():
    db = _make_db(snap_rows=[])
    result = _run(build_readiness_diagnostic(_UID, db_client=db, _policy_config=None))

    assert "snapshot" in result["next_required_action"].lower()


# ── Gate B: stale snapshot ────────────────────────────────────────────────────


def test_stale_snapshot_sizing_values_not_ready():
    snap = _enriched_snapshot(snapshot_at=_stale_iso())
    db = _make_db(snap_rows=[snap])
    result = _run(build_readiness_diagnostic(_UID, db_client=db, _policy_config=None))

    assert result["sizing_values_ready"] is False
    assert result["snapshot"]["status"] == "stale"


def test_stale_snapshot_next_action_refresh():
    snap = _enriched_snapshot(snapshot_at=_stale_iso())
    db = _make_db(snap_rows=[snap])
    result = _run(build_readiness_diagnostic(_UID, db_client=db, _policy_config=None))

    assert "24 hours" in result["next_required_action"]


def test_stale_snapshot_metadata_present():
    snap = _enriched_snapshot(snap_id="stale-snap", snapshot_at=_stale_iso())
    db = _make_db(snap_rows=[snap])
    result = _run(build_readiness_diagnostic(_UID, db_client=db, _policy_config=None))

    assert result["snapshot"]["present"] is True
    assert result["snapshot"]["snapshot_id"] == "stale-snap"
    assert result["snapshot"]["age_hours"] is not None
    assert result["snapshot"]["age_hours"] > STALE_THRESHOLD_HOURS


# ── Gate C: legacy snapshot — no market_value_usd ────────────────────────────


def test_legacy_snapshot_missing_market_values():
    """Fresh snapshot but positions_data has no market_value_usd field."""
    snap = {
        "id": "legacy-snap",
        "snapshot_at": _fresh_iso(),
        "total_equity": 100_000.0,
        "cash_balance": 5_000.0,
        "positions_data": [
            {"ticker": "AAPL", "shares": 500, "avg_cost": 150.0}
        ],
    }
    db = _make_db(snap_rows=[snap])
    result = _run(build_readiness_diagnostic(_UID, db_client=db, _policy_config=None))

    assert result["sizing_values_ready"] is False
    assert result["snapshot"]["status"] == "fresh"
    assert "AAPL" in result["market_values"]["uncertified_tickers"]
    assert result["market_values"]["all_positions_have_market_value"] is False


def test_legacy_snapshot_next_action_mentions_market_values():
    snap = {
        "id": "legacy-snap",
        "snapshot_at": _fresh_iso(),
        "total_equity": 100_000.0,
        "cash_balance": 5_000.0,
        "positions_data": [{"ticker": "AAPL", "shares": 500, "avg_cost": 150.0}],
    }
    db = _make_db(snap_rows=[snap])
    result = _run(build_readiness_diagnostic(_UID, db_client=db, _policy_config=None))

    assert "AAPL" in result["next_required_action"]
    assert "market value" in result["next_required_action"].lower()


# ── Gate D: missing target allocations ───────────────────────────────────────


def test_enriched_snapshot_no_target_allocations():
    snap = _enriched_snapshot()
    db = _make_db(snap_rows=[snap], target_rows=[])
    result = _run(build_readiness_diagnostic(_UID, db_client=db, _policy_config=None))

    assert result["target_allocation_ready"] is False
    assert result["sizing_values_ready"] is True
    assert "AAPL" in result["target_allocations"]["missing_tickers"]
    assert result["target_allocations"]["unique_tickers_in_db"] == 0


def test_missing_target_next_action_mentions_add_allocations():
    snap = _enriched_snapshot()
    db = _make_db(snap_rows=[snap], target_rows=[])
    result = _run(build_readiness_diagnostic(_UID, db_client=db, _policy_config=None))

    assert "AAPL" in result["next_required_action"]
    assert "98%" in result["next_required_action"]


# ── Gate E: target total under 98% ───────────────────────────────────────────


def test_target_total_under_98_pct_suppresses_readiness():
    snap = _enriched_snapshot()
    # 90% total — below MIN
    db = _make_db(snap_rows=[snap], target_rows=[_target_row("AAPL", 90.0)])
    result = _run(
        build_readiness_diagnostic(_UID, db_client=db, _policy_config=_CERTIFIED_POLICY)
    )

    assert result["target_allocation_ready"] is False
    assert result["exact_dollar_ready"] is False
    assert "TARGET_ALLOCATION_TOTAL_UNDERALLOCATED" in result["suppression_reasons"]
    assert result["target_allocations"]["target_total_pct"] == pytest.approx(90.0, abs=0.01)
    assert result["target_allocations"]["target_total_in_range"] is False


def test_target_total_under_98_next_action_adjust():
    snap = _enriched_snapshot()
    db = _make_db(snap_rows=[snap], target_rows=[_target_row("AAPL", 90.0)])
    result = _run(
        build_readiness_diagnostic(_UID, db_client=db, _policy_config=_CERTIFIED_POLICY)
    )

    assert "90.0%" in result["next_required_action"]
    assert "98%" in result["next_required_action"]


# ── Gate F: target total over 102% ───────────────────────────────────────────


def test_target_total_over_102_pct_suppresses_readiness():
    # Use 2 positions so each individual weight is valid (≤1.0) but total exceeds 102%.
    # AAPL=60% + MSFT=45% = 105% → OVERALLOCATED
    positions = [
        {"ticker": "AAPL", "market_value_usd": 60_000.0},
        {"ticker": "MSFT", "market_value_usd": 40_000.0},
    ]
    snap = _enriched_snapshot(positions=positions)
    db = _make_db(
        snap_rows=[snap],
        target_rows=[_target_row("AAPL", 60.0), _target_row("MSFT", 45.0)],
    )
    result = _run(
        build_readiness_diagnostic(_UID, db_client=db, _policy_config=_CERTIFIED_POLICY)
    )

    assert result["target_allocation_ready"] is False
    assert result["exact_dollar_ready"] is False
    assert "TARGET_ALLOCATION_TOTAL_OVERALLOCATED" in result["suppression_reasons"]
    assert result["target_allocations"]["target_total_in_range"] is False
    assert result["target_allocations"]["target_total_pct"] == pytest.approx(105.0, abs=0.01)


def test_target_total_over_102_next_action_adjust():
    positions = [
        {"ticker": "AAPL", "market_value_usd": 60_000.0},
        {"ticker": "MSFT", "market_value_usd": 40_000.0},
    ]
    snap = _enriched_snapshot(positions=positions)
    db = _make_db(
        snap_rows=[snap],
        target_rows=[_target_row("AAPL", 60.0), _target_row("MSFT", 45.0)],
    )
    result = _run(
        build_readiness_diagnostic(_UID, db_client=db, _policy_config=_CERTIFIED_POLICY)
    )

    assert "105.0%" in result["next_required_action"]
    assert "102%" in result["next_required_action"]


# ── Gate G: invalid / missing policy config ───────────────────────────────────


def test_missing_policy_suppresses_policy_ready():
    snap = _enriched_snapshot()
    db = _make_db(snap_rows=[snap], target_rows=[_target_row("AAPL", 100.0)])
    result = _run(build_readiness_diagnostic(_UID, db_client=db, _policy_config=None))

    assert result["policy_ready"] is False
    assert result["exact_dollar_ready"] is False
    assert result["policy"]["policy_valid"] is False
    assert "MINIMUM_TRADE_UNSUPPORTED" in result["suppression_reasons"]


def test_missing_policy_next_action_set_env_vars():
    snap = _enriched_snapshot()
    db = _make_db(snap_rows=[snap], target_rows=[_target_row("AAPL", 100.0)])
    result = _run(build_readiness_diagnostic(_UID, db_client=db, _policy_config=None))

    assert "DEPLOY_MINIMUM_TRADE_USD" in result["next_required_action"]
    assert "DEPLOY_ROUNDING_POLICY" in result["next_required_action"]


# ── Gate H: all gates ready ───────────────────────────────────────────────────


def test_all_gates_ready_exact_dollar_ready():
    snap = _enriched_snapshot()
    db = _make_db(snap_rows=[snap], target_rows=[_target_row("AAPL", 100.0)])
    result = _run(
        build_readiness_diagnostic(_UID, db_client=db, _policy_config=_CERTIFIED_POLICY)
    )

    assert result["exact_dollar_ready"] is True
    assert result["sizing_values_ready"] is True
    assert result["target_allocation_ready"] is True
    assert result["policy_ready"] is True


def test_all_gates_ready_next_action_says_ready():
    snap = _enriched_snapshot()
    db = _make_db(snap_rows=[snap], target_rows=[_target_row("AAPL", 100.0)])
    result = _run(
        build_readiness_diagnostic(_UID, db_client=db, _policy_config=_CERTIFIED_POLICY)
    )

    assert "ready" in result["next_required_action"].lower()
    assert result["suppression_reasons"] == []


def test_all_gates_ready_target_total_reported():
    snap = _enriched_snapshot()
    db = _make_db(snap_rows=[snap], target_rows=[_target_row("AAPL", 100.0)])
    result = _run(
        build_readiness_diagnostic(_UID, db_client=db, _policy_config=_CERTIFIED_POLICY)
    )

    assert result["target_allocations"]["target_total_pct"] == pytest.approx(100.0, abs=0.01)
    assert result["target_allocations"]["target_total_in_range"] is True


# ── Gate I: no secret values exposed ─────────────────────────────────────────


def test_policy_section_does_not_expose_minimum_trade_value():
    """Policy section must report configured/not without exposing env var values."""
    snap = _enriched_snapshot()
    db = _make_db(snap_rows=[snap], target_rows=[_target_row("AAPL", 100.0)])
    result = _run(
        build_readiness_diagnostic(_UID, db_client=db, _policy_config=_CERTIFIED_POLICY)
    )

    policy = result["policy"]
    # Numeric value must NOT appear — only structural keys
    assert "minimum_trade_usd" not in policy
    assert "rounding_policy" not in policy
    assert set(policy.keys()) == {
        "minimum_trade_configured",
        "rounding_policy_configured",
        "policy_valid",
    }


# ── Gate J: conflicting target allocations ────────────────────────────────────


def test_conflicting_target_allocations_suppresses_readiness():
    snap = _enriched_snapshot()
    # Two rows for AAPL → adapter marks CONFLICTING
    db = _make_db(
        snap_rows=[snap],
        target_rows=[_target_row("AAPL", 60.0), _target_row("AAPL", 40.0)],
    )
    result = _run(
        build_readiness_diagnostic(_UID, db_client=db, _policy_config=_CERTIFIED_POLICY)
    )

    assert result["exact_dollar_ready"] is False
    assert "AAPL" in result["target_allocations"]["conflicting_tickers"]
    assert "TARGET_ALLOCATION_CONFLICTING" in result["suppression_reasons"]


def test_conflicting_target_next_action_remove_duplicates():
    snap = _enriched_snapshot()
    db = _make_db(
        snap_rows=[snap],
        target_rows=[_target_row("AAPL", 60.0), _target_row("AAPL", 40.0)],
    )
    result = _run(
        build_readiness_diagnostic(_UID, db_client=db, _policy_config=_CERTIFIED_POLICY)
    )

    assert "AAPL" in result["next_required_action"]
    assert "duplicate" in result["next_required_action"].lower()


# ── Gate K: no provider / LLM / broker / legacy engine calls ─────────────────


def test_diagnostic_module_has_no_provider_imports():
    """Confirm the diagnostic module does not import live-provider, LLM, or broker code."""
    import app.services.deploy.deploy_readiness_diagnostic_v1 as mod
    src = inspect.getsource(mod)
    # Only scan import lines — docstring text like "No providers" is fine.
    import_lines = "\n".join(
        line for line in src.splitlines()
        if line.strip().startswith(("import ", "from "))
    ).lower()

    forbidden = ["provider", "openai", "anthropic", "broker", "allocation_plan", "live_price"]
    for term in forbidden:
        assert term not in import_lines, (
            f"Diagnostic module must not import '{term}' — found in import lines"
        )


# ── Gate L: snapshot metadata in response ─────────────────────────────────────


def test_fresh_snapshot_metadata_included():
    ts = _fresh_iso()
    snap = _enriched_snapshot(snap_id="snap-xyz", snapshot_at=ts)
    db = _make_db(snap_rows=[snap], target_rows=[_target_row("AAPL", 100.0)])
    result = _run(
        build_readiness_diagnostic(_UID, db_client=db, _policy_config=_CERTIFIED_POLICY)
    )

    assert result["snapshot"]["present"] is True
    assert result["snapshot"]["snapshot_id"] == "snap-xyz"
    assert result["snapshot"]["snapshot_at"] is not None
    assert result["snapshot"]["age_hours"] is not None
    assert result["snapshot"]["age_hours"] < STALE_THRESHOLD_HOURS
    assert result["snapshot"]["status"] == "fresh"


def test_market_values_position_count_reported():
    positions = [
        {"ticker": "AAPL", "market_value_usd": 60_000.0},
        {"ticker": "MSFT", "market_value_usd": 40_000.0},
    ]
    snap = _enriched_snapshot(positions=positions)
    db = _make_db(
        snap_rows=[snap],
        target_rows=[_target_row("AAPL", 60.0), _target_row("MSFT", 40.0)],
    )
    result = _run(
        build_readiness_diagnostic(_UID, db_client=db, _policy_config=_CERTIFIED_POLICY)
    )

    assert result["market_values"]["position_count"] == 2
    assert result["market_values"]["all_positions_have_market_value"] is True
    assert result["market_values"]["uncertified_tickers"] == []


# ── _next_required_action unit tests ─────────────────────────────────────────


def _snap(status: str) -> dict:
    return {"status": status}


def _mv(ok: bool, tickers: list | None = None) -> dict:
    return {"all_positions_have_market_value": ok, "uncertified_tickers": tickers or []}


def _ta(
    conflicting: list | None = None,
    missing: list | None = None,
    total_pct: float | None = None,
    in_range: bool | None = None,
) -> dict:
    return {
        "conflicting_tickers": conflicting or [],
        "missing_tickers": missing or [],
        "target_total_pct": total_pct,
        "target_total_in_range": in_range,
    }


def _pol(valid: bool) -> dict:
    return {
        "minimum_trade_configured": valid,
        "rounding_policy_configured": valid,
        "policy_valid": valid,
    }


def test_next_action_missing_snapshot():
    action = _next_required_action(_snap("missing"), _mv(True), _ta(), _pol(True))
    assert "snapshot" in action.lower()


def test_next_action_stale_snapshot():
    action = _next_required_action(_snap("stale"), _mv(True), _ta(), _pol(True))
    assert "24 hours" in action


def test_next_action_missing_market_values():
    action = _next_required_action(_snap("fresh"), _mv(False, ["AAPL"]), _ta(), _pol(True))
    assert "AAPL" in action


def test_next_action_conflicting_tickers():
    action = _next_required_action(
        _snap("fresh"), _mv(True), _ta(conflicting=["TSLA"]), _pol(True)
    )
    assert "TSLA" in action
    assert "duplicate" in action.lower()


def test_next_action_missing_tickers():
    action = _next_required_action(
        _snap("fresh"), _mv(True), _ta(missing=["NVDA"]), _pol(True)
    )
    assert "NVDA" in action
    assert "98%" in action


def test_next_action_total_under_98():
    action = _next_required_action(
        _snap("fresh"), _mv(True), _ta(total_pct=85.0, in_range=False), _pol(True)
    )
    assert "85.0%" in action
    assert "98%" in action


def test_next_action_total_over_102():
    action = _next_required_action(
        _snap("fresh"), _mv(True), _ta(total_pct=110.0, in_range=False), _pol(True)
    )
    assert "110.0%" in action
    assert "102%" in action


def test_next_action_policy_missing():
    action = _next_required_action(
        _snap("fresh"), _mv(True), _ta(total_pct=100.0, in_range=True), _pol(False)
    )
    assert "DEPLOY_MINIMUM_TRADE_USD" in action


def test_next_action_all_ready():
    action = _next_required_action(
        _snap("fresh"), _mv(True), _ta(total_pct=100.0, in_range=True), _pol(True)
    )
    assert "ready" in action.lower()
