"""Build 3 PR 2A Hotfix 2 — Watchtower must republish when Intel becomes eligible.

Root cause: compare_and_republish() was only called inside the price-snapshot-persist
path. When analyst jobs drained and intel_eligible=True / stale_types=none /
analyst_jobs_enqueued=0, no code path triggered the Intel republisher.

Fix: Step 7 added to run_refresh_cycle(). When eligibility conditions are met and
republish has not already run this cycle, calls republish_after_analyst_eligibility()
using the max evidence as_of timestamp for idempotency instead of portfolio_snapshots.

Proves:
  1. Stale recommendation jobs drain to zero, Watchtower calls republisher once.
  2. intel_eligible=True + stale_types=none + no new evidence => skipped_no_new_evidence.
  3. Certification failure logs certification_blocked; does not overwrite prior snapshot.
  4. No repeated publish every interval without new evidence (idempotency).
  5. No direct decide() import in watchtower_background_refresh_worker_v1.
  6. Step 7 skipped when analyst_jobs_enqueued > 0 (jobs still in flight).
  7. Step 7 skipped when intel_republish_result already set (price path already ran).
  8. Step 7 skipped on complete price failure (all failed, none succeeded).
  9. republish_after_analyst_eligibility: no snapshot exists → no_snapshot_exists.
  10. republish_after_analyst_eligibility: no latest_evidence_at → skipped_no_new_evidence.
  11. republish_after_analyst_eligibility: no callable + evidence newer → republish_pending.
  12. _max_evidence_at: returns max as_of across specified evidence types only.
  13. PUBLISH_SKIPPED_NO_NEW_EVIDENCE constant exported from republisher module.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

UID = uuid.UUID("aabbccdd-0000-0000-0000-000000000001")
NOW = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)

# Analyst evidence was written 30 minutes ago (fresh, within SLA).
ANALYST_EVIDENCE_AT = NOW - timedelta(minutes=30)
# Intel snapshot was last generated 2 hours ago (before analyst evidence).
INTEL_SNAPSHOT_AT = NOW - timedelta(hours=2)
# Intel snapshot generated AFTER analyst evidence (idempotency case).
INTEL_SNAPSHOT_AT_RECENT = NOW - timedelta(minutes=10)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_fresh_evidence_records():
    """Return a list of fresh analyst_llm + recommendation EvidenceRecords."""
    from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import (
        EVIDENCE_TYPE_ANALYST_LLM,
        EVIDENCE_TYPE_RECOMMENDATION,
        FRESHNESS_FRESH,
        EvidenceRecord,
    )
    return [
        EvidenceRecord(
            evidence_type=EVIDENCE_TYPE_ANALYST_LLM,
            ticker="AAPL",
            scope="ticker",
            as_of=ANALYST_EVIDENCE_AT,
            collected_at=ANALYST_EVIDENCE_AT,
            source="agent_insights",
            freshness_status=FRESHNESS_FRESH,
            freshness_sla_seconds=86400,
            deploy_eligible=True,
            decision_eligible=True,
            reason=None,
        ),
        EvidenceRecord(
            evidence_type=EVIDENCE_TYPE_RECOMMENDATION,
            ticker="AAPL",
            scope="ticker",
            as_of=ANALYST_EVIDENCE_AT,
            collected_at=ANALYST_EVIDENCE_AT,
            source="insight_cards",
            freshness_status=FRESHNESS_FRESH,
            freshness_sla_seconds=28800,
            deploy_eligible=True,
            decision_eligible=True,
            reason=None,
        ),
    ]


def _make_intel_snapshot_client(*, generated_at: str) -> MagicMock:
    """Return a mock Supabase client with an intel_v3_snapshots row."""
    client = MagicMock()
    snap_id = str(uuid.uuid4())

    def _table(name):
        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.order.return_value = chain
        chain.limit.return_value = chain
        if name == "intel_v3_snapshots":
            chain.execute.return_value = MagicMock(data=[
                {"payload": {
                    "snapshot_id": snap_id,
                    "generated_at": generated_at,
                    "snapshot_source": "worker_certified",
                }}
            ])
        else:
            chain.execute.return_value = MagicMock(data=[])
        return chain

    client.table = _table
    return client


# ── 1. Republisher called once after analyst jobs drain ───────────────────────

class TestRepublisherCalledAfterAnalystDrain:

    @pytest.mark.asyncio
    async def test_republisher_called_when_eligible_and_stale_types_empty(self):
        """After analyst jobs drain, Watchtower calls intel republisher once."""
        from app.services.intelligence.v3.watchtower_background_refresh_worker_v1 import (
            WatchtowerBackgroundRefreshWorker,
        )

        fresh_records = _make_fresh_evidence_records()
        mock_client = MagicMock()
        mock_republish = AsyncMock(return_value={"snapshot_source": "worker_certified"})

        intel_client = _make_intel_snapshot_client(
            generated_at=INTEL_SNAPSHOT_AT.isoformat()
        )

        with (
            patch(
                "app.services.intelligence.v3.watchtower_background_refresh_worker_v1"
                ".collect_evidence_records",
                return_value=fresh_records,
            ),
            patch(
                "app.services.intelligence.v3.watchtower_intel_republisher_v1"
                "._fetch_latest_intel_snapshot",
                return_value={
                    "snapshot_id": "snap-001",
                    "generated_at": INTEL_SNAPSHOT_AT.isoformat(),
                    "snapshot_source": "worker_certified",
                },
            ),
        ):
            worker = WatchtowerBackgroundRefreshWorker(
                client=mock_client,
                intel_republish_callable=mock_republish,
            )
            result = await worker.run_refresh_cycle(UID, now=NOW)

        mock_republish.assert_called_once()
        assert result.intel_republish_result is not None
        assert result.intel_republish_result["publish_status"] == "rebuilt_and_published"

    @pytest.mark.asyncio
    async def test_republisher_called_exactly_once_not_twice(self):
        """Republisher is called exactly once per cycle, not once per eligible record."""
        from app.services.intelligence.v3.watchtower_background_refresh_worker_v1 import (
            WatchtowerBackgroundRefreshWorker,
        )

        fresh_records = _make_fresh_evidence_records()
        call_count = []

        async def _counting_republish(user_id):
            call_count.append(1)
            return {"snapshot_source": "worker_certified"}

        with (
            patch(
                "app.services.intelligence.v3.watchtower_background_refresh_worker_v1"
                ".collect_evidence_records",
                return_value=fresh_records,
            ),
            patch(
                "app.services.intelligence.v3.watchtower_intel_republisher_v1"
                "._fetch_latest_intel_snapshot",
                return_value={
                    "snapshot_id": "snap-001",
                    "generated_at": INTEL_SNAPSHOT_AT.isoformat(),
                    "snapshot_source": "worker_certified",
                },
            ),
        ):
            worker = WatchtowerBackgroundRefreshWorker(
                client=MagicMock(),
                intel_republish_callable=_counting_republish,
            )
            await worker.run_refresh_cycle(UID, now=NOW)

        assert len(call_count) == 1, f"expected 1 republish call, got {len(call_count)}"


# ── 2. Skipped when no new evidence (idempotency) ────────────────────────────

class TestSkippedNoNewEvidence:

    @pytest.mark.asyncio
    async def test_skipped_when_intel_snapshot_newer_than_evidence(self):
        """When Intel snapshot was generated after the latest analyst evidence,
        publish_status=skipped_no_new_evidence and republisher is not called."""
        from app.services.intelligence.v3.watchtower_intel_republisher_v1 import (
            PUBLISH_SKIPPED_NO_NEW_EVIDENCE,
            republish_after_analyst_eligibility,
        )

        mock_republish = AsyncMock(return_value={"snapshot_source": "worker_certified"})

        with patch(
            "app.services.intelligence.v3.watchtower_intel_republisher_v1"
            "._fetch_latest_intel_snapshot",
            return_value={
                "snapshot_id": "snap-recent",
                "generated_at": INTEL_SNAPSHOT_AT_RECENT.isoformat(),
                "snapshot_source": "worker_certified",
            },
        ):
            result = await republish_after_analyst_eligibility(
                UID,
                MagicMock(),
                intel_republish_callable=mock_republish,
                # Evidence is 30 min old; Intel snapshot was generated 10 min ago
                latest_evidence_at=ANALYST_EVIDENCE_AT,
                now=NOW,
            )

        assert result.publish_status == PUBLISH_SKIPPED_NO_NEW_EVIDENCE
        mock_republish.assert_not_called()
        assert result.evidence_newer_than_certified_snapshot is False

    @pytest.mark.asyncio
    async def test_worker_skips_when_intel_snapshot_already_current(self):
        """Worker-level test: if Intel snapshot is newer than analyst evidence,
        cycle result has publish_status=skipped_no_new_evidence."""
        from app.services.intelligence.v3.watchtower_background_refresh_worker_v1 import (
            WatchtowerBackgroundRefreshWorker,
        )
        from app.services.intelligence.v3.watchtower_intel_republisher_v1 import (
            PUBLISH_SKIPPED_NO_NEW_EVIDENCE,
        )

        fresh_records = _make_fresh_evidence_records()
        mock_republish = AsyncMock(return_value={"snapshot_source": "worker_certified"})

        with (
            patch(
                "app.services.intelligence.v3.watchtower_background_refresh_worker_v1"
                ".collect_evidence_records",
                return_value=fresh_records,
            ),
            patch(
                "app.services.intelligence.v3.watchtower_intel_republisher_v1"
                "._fetch_latest_intel_snapshot",
                return_value={
                    "snapshot_id": "snap-recent",
                    # Intel snapshot generated 10 min ago; analyst evidence is 30 min old
                    "generated_at": INTEL_SNAPSHOT_AT_RECENT.isoformat(),
                    "snapshot_source": "worker_certified",
                },
            ),
        ):
            worker = WatchtowerBackgroundRefreshWorker(
                client=MagicMock(),
                intel_republish_callable=mock_republish,
            )
            result = await worker.run_refresh_cycle(UID, now=NOW)

        assert result.intel_republish_result is not None
        assert result.intel_republish_result["publish_status"] == PUBLISH_SKIPPED_NO_NEW_EVIDENCE
        mock_republish.assert_not_called()


# ── 3. Certification failure → certification_blocked, no snapshot overwrite ──

class TestCertificationBlocked:

    @pytest.mark.asyncio
    async def test_certification_failure_logs_blocked_status(self):
        """When republish callable raises, publish_status=certification_blocked."""
        from app.services.intelligence.v3.watchtower_intel_republisher_v1 import (
            PUBLISH_CERTIFICATION_BLOCKED,
            republish_after_analyst_eligibility,
        )

        failing_republish = AsyncMock(side_effect=RuntimeError("certification_failed"))

        with patch(
            "app.services.intelligence.v3.watchtower_intel_republisher_v1"
            "._fetch_latest_intel_snapshot",
            return_value={
                "snapshot_id": "snap-old",
                "generated_at": INTEL_SNAPSHOT_AT.isoformat(),
                "snapshot_source": "worker_certified",
            },
        ):
            result = await republish_after_analyst_eligibility(
                UID,
                MagicMock(),
                intel_republish_callable=failing_republish,
                latest_evidence_at=ANALYST_EVIDENCE_AT,
                now=NOW,
            )

        assert result.publish_status == PUBLISH_CERTIFICATION_BLOCKED
        assert result.error is not None
        assert "certification_failed" in result.error

    @pytest.mark.asyncio
    async def test_certification_source_not_worker_certified_logs_blocked(self):
        """When republish returns certification_failed source, status=certification_blocked."""
        from app.services.intelligence.v3.watchtower_intel_republisher_v1 import (
            PUBLISH_CERTIFICATION_BLOCKED,
            republish_after_analyst_eligibility,
        )

        non_certified = AsyncMock(return_value={"snapshot_source": "certification_failed"})

        with patch(
            "app.services.intelligence.v3.watchtower_intel_republisher_v1"
            "._fetch_latest_intel_snapshot",
            return_value={
                "snapshot_id": "snap-old",
                "generated_at": INTEL_SNAPSHOT_AT.isoformat(),
                "snapshot_source": "worker_certified",
            },
        ):
            result = await republish_after_analyst_eligibility(
                UID,
                MagicMock(),
                intel_republish_callable=non_certified,
                latest_evidence_at=ANALYST_EVIDENCE_AT,
                now=NOW,
            )

        assert result.publish_status == PUBLISH_CERTIFICATION_BLOCKED


# ── 4. No repeated publish without new evidence ───────────────────────────────

class TestNoRepeatedPublish:

    @pytest.mark.asyncio
    async def test_no_publish_after_recent_republish(self):
        """Two consecutive cycles: first publishes, second skips (idempotency)."""
        from app.services.intelligence.v3.watchtower_intel_republisher_v1 import (
            PUBLISH_REBUILT_AND_PUBLISHED,
            PUBLISH_SKIPPED_NO_NEW_EVIDENCE,
            republish_after_analyst_eligibility,
        )

        call_count = []

        async def _republish(user_id):
            call_count.append(1)
            return {"snapshot_source": "worker_certified"}

        # Cycle 1: evidence (30 min ago) is newer than Intel snapshot (2 h ago)
        with patch(
            "app.services.intelligence.v3.watchtower_intel_republisher_v1"
            "._fetch_latest_intel_snapshot",
            return_value={
                "snapshot_id": "snap-old",
                "generated_at": INTEL_SNAPSHOT_AT.isoformat(),
            },
        ):
            result1 = await republish_after_analyst_eligibility(
                UID,
                MagicMock(),
                intel_republish_callable=_republish,
                latest_evidence_at=ANALYST_EVIDENCE_AT,
                now=NOW,
            )

        assert result1.publish_status == PUBLISH_REBUILT_AND_PUBLISHED
        assert len(call_count) == 1

        # Cycle 2: Intel snapshot now generated at NOW (just published above).
        # Analyst evidence (30 min ago) is OLDER than the new snapshot → skip.
        with patch(
            "app.services.intelligence.v3.watchtower_intel_republisher_v1"
            "._fetch_latest_intel_snapshot",
            return_value={
                "snapshot_id": "snap-new",
                "generated_at": NOW.isoformat(),
            },
        ):
            result2 = await republish_after_analyst_eligibility(
                UID,
                MagicMock(),
                intel_republish_callable=_republish,
                latest_evidence_at=ANALYST_EVIDENCE_AT,
                now=NOW,
            )

        assert result2.publish_status == PUBLISH_SKIPPED_NO_NEW_EVIDENCE
        assert len(call_count) == 1, "republish must not be called a second time"


# ── 5. No direct decide() import in worker ───────────────────────────────────

class TestNoBoundaryViolation:

    def test_worker_module_does_not_import_decide(self):
        """watchtower_background_refresh_worker_v1 must NOT import decide()."""
        import importlib
        import sys

        mod_name = (
            "app.services.intelligence.v3"
            ".watchtower_background_refresh_worker_v1"
        )
        mod = sys.modules.get(mod_name) or importlib.import_module(mod_name)
        source = mod.__file__
        assert source is not None
        with open(source) as f:
            contents = f.read()
        assert "from .decision_policy_v1 import decide" not in contents, (
            "worker must not import decide() — boundary violation"
        )
        assert "import decide" not in contents, (
            "worker must not import decide() — boundary violation"
        )


# ── 6. Step 7 conditions: analyst_jobs_enqueued > 0 skips republish ──────────

class TestStep7Conditions:

    @pytest.mark.asyncio
    async def test_republish_skipped_when_analyst_jobs_enqueued(self):
        """Step 7 does not fire when analyst_jobs_enqueued > 0 (jobs still in flight)."""
        from app.services.intelligence.v3.watchtower_background_refresh_worker_v1 import (
            WatchtowerBackgroundRefreshWorker,
        )
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import (
            EVIDENCE_TYPE_ANALYST_LLM,
            FRESHNESS_STALE,
            EvidenceRecord,
        )

        stale_llm = EvidenceRecord(
            evidence_type=EVIDENCE_TYPE_ANALYST_LLM,
            ticker="AAPL",
            scope="ticker",
            as_of=NOW - timedelta(hours=30),
            collected_at=NOW - timedelta(hours=30),
            source="agent_insights",
            freshness_status=FRESHNESS_STALE,
            freshness_sla_seconds=86400,
            deploy_eligible=False,
            decision_eligible=False,
            reason="stale",
        )
        mock_republish = AsyncMock(return_value={"snapshot_source": "worker_certified"})
        mock_enqueue = AsyncMock(return_value=5)

        with patch(
            "app.services.intelligence.v3.watchtower_background_refresh_worker_v1"
            ".collect_evidence_records",
            return_value=[stale_llm],
        ):
            worker = WatchtowerBackgroundRefreshWorker(
                client=MagicMock(),
                analyst_job_enqueue_callable=mock_enqueue,
                intel_republish_callable=mock_republish,
            )
            result = await worker.run_refresh_cycle(UID, now=NOW)

        assert result.analyst_jobs_enqueued > 0
        # Step 7 must not fire because analyst_jobs_enqueued > 0
        assert result.intel_republish_result is None, (
            "republish must not be triggered when analyst jobs are still being enqueued"
        )
        mock_republish.assert_not_called()

    @pytest.mark.asyncio
    async def test_republish_skipped_on_complete_price_failure(self):
        """Step 7 does not fire when all price tickers failed (no successes)."""
        from app.services.intelligence.v3.watchtower_background_refresh_worker_v1 import (
            WatchtowerBackgroundRefreshWorker,
        )
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import (
            EVIDENCE_TYPE_PRICE,
            FRESHNESS_STALE,
            EvidenceRecord,
        )

        # Stale price record so price refresh path is entered
        stale_price = EvidenceRecord(
            evidence_type=EVIDENCE_TYPE_PRICE,
            ticker="AAPL",
            scope="ticker",
            as_of=NOW - timedelta(hours=2),
            collected_at=NOW - timedelta(hours=2),
            source="price_service",
            freshness_status=FRESHNESS_STALE,
            freshness_sla_seconds=900,
            deploy_eligible=False,
            decision_eligible=False,
            reason="stale",
        )
        mock_republish = AsyncMock(return_value={"snapshot_source": "worker_certified"})
        # Price refresh returns all None (complete failure)
        mock_price = AsyncMock(return_value={"AAPL": None})

        with patch(
            "app.services.intelligence.v3.watchtower_background_refresh_worker_v1"
            ".collect_evidence_records",
            return_value=[stale_price],
        ):
            worker = WatchtowerBackgroundRefreshWorker(
                client=MagicMock(),
                price_refresh_callable=mock_price,
                intel_republish_callable=mock_republish,
            )
            result = await worker.run_refresh_cycle(UID, now=NOW)

        # Complete price failure: failed_price_tickers=[AAPL], refreshed=[]
        assert result.failed_price_tickers == ["AAPL"]
        assert result.refreshed_price_tickers == []
        # Step 7 must be blocked (and price-path republish also did not fire
        # because snapshot persists requires at least one succeeded ticker)
        assert result.intel_republish_result is None, (
            "republish must not fire when all price tickers failed"
        )


# ── 7. Step 7 skipped when republish already set (price path ran) ─────────────

class TestStep7SkippedIfAlreadyRun:

    @pytest.mark.asyncio
    async def test_step7_skipped_if_price_path_already_republished(self):
        """Step 7 does not fire if intel_republish_result was already set by the
        price-snapshot-persist path in the same cycle."""
        from app.services.intelligence.v3.watchtower_background_refresh_worker_v1 import (
            WatchtowerBackgroundRefreshWorker,
        )
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import (
            EVIDENCE_TYPE_PRICE,
            FRESHNESS_STALE,
            EvidenceRecord,
        )

        stale_price = EvidenceRecord(
            evidence_type=EVIDENCE_TYPE_PRICE,
            ticker="AAPL",
            scope="ticker",
            as_of=NOW - timedelta(hours=2),
            collected_at=NOW - timedelta(hours=2),
            source="price_service",
            freshness_status=FRESHNESS_STALE,
            freshness_sla_seconds=900,
            deploy_eligible=False,
            decision_eligible=False,
            reason="stale",
        )

        call_count = []

        async def _republish(user_id):
            call_count.append(1)
            return {"snapshot_source": "worker_certified"}

        mock_price = AsyncMock(return_value={"AAPL": 150.0})

        # Patch persist_watchtower_price_snapshot at its definition module
        # (deferred import in worker uses this path).
        with (
            patch(
                "app.services.intelligence.v3.watchtower_background_refresh_worker_v1"
                ".collect_evidence_records",
                return_value=[stale_price],
            ),
            patch(
                "app.services.intelligence.v3.watchtower_price_snapshot_writer_v1"
                ".persist_watchtower_price_snapshot",
                new_callable=AsyncMock,
            ) as mock_persist,
        ):
            mock_persist.return_value = MagicMock(
                persisted=True,
                certified_ticker_count=1,
                carried_ticker_count=0,
            )
            with patch(
                "app.services.intelligence.v3.watchtower_intel_republisher_v1"
                "._fetch_latest_intel_snapshot",
                return_value={
                    "snapshot_id": "snap-old",
                    "generated_at": INTEL_SNAPSHOT_AT.isoformat(),
                },
            ):
                with patch(
                    "app.services.intelligence.v3.watchtower_intel_republisher_v1"
                    "._fetch_latest_portfolio_snapshot",
                    return_value={
                        "id": "port-001",
                        "snapshot_at": (NOW - timedelta(minutes=5)).isoformat(),
                    },
                ):
                    worker = WatchtowerBackgroundRefreshWorker(
                        client=MagicMock(),
                        price_refresh_callable=mock_price,
                        intel_republish_callable=_republish,
                    )
                    result = await worker.run_refresh_cycle(UID, now=NOW)

        # Price path may have set intel_republish_result; if so, Step 7 must not add another call
        assert len(call_count) <= 1, (
            f"republish callable must be called at most once per cycle, got {len(call_count)}"
        )


# ── 8. republish_after_analyst_eligibility edge cases ────────────────────────

class TestRepublishAfterAnalystEligibilityEdgeCases:

    @pytest.mark.asyncio
    async def test_no_snapshot_exists_returns_no_snapshot_exists(self):
        """When no Intel snapshot is found, returns no_snapshot_exists."""
        from app.services.intelligence.v3.watchtower_intel_republisher_v1 import (
            PUBLISH_NO_SNAPSHOT_EXISTS,
            republish_after_analyst_eligibility,
        )

        mock_republish = AsyncMock()

        with patch(
            "app.services.intelligence.v3.watchtower_intel_republisher_v1"
            "._fetch_latest_intel_snapshot",
            return_value=None,
        ):
            result = await republish_after_analyst_eligibility(
                UID,
                MagicMock(),
                intel_republish_callable=mock_republish,
                latest_evidence_at=ANALYST_EVIDENCE_AT,
                now=NOW,
            )

        assert result.publish_status == PUBLISH_NO_SNAPSHOT_EXISTS
        mock_republish.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_latest_evidence_at_returns_skipped(self):
        """When latest_evidence_at=None, returns skipped_no_new_evidence (safe default)."""
        from app.services.intelligence.v3.watchtower_intel_republisher_v1 import (
            PUBLISH_SKIPPED_NO_NEW_EVIDENCE,
            republish_after_analyst_eligibility,
        )

        mock_republish = AsyncMock()

        with patch(
            "app.services.intelligence.v3.watchtower_intel_republisher_v1"
            "._fetch_latest_intel_snapshot",
            return_value={
                "snapshot_id": "snap-old",
                "generated_at": INTEL_SNAPSHOT_AT.isoformat(),
            },
        ):
            result = await republish_after_analyst_eligibility(
                UID,
                MagicMock(),
                intel_republish_callable=mock_republish,
                latest_evidence_at=None,
                now=NOW,
            )

        assert result.publish_status == PUBLISH_SKIPPED_NO_NEW_EVIDENCE
        mock_republish.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_callable_but_evidence_newer_returns_republish_pending(self):
        """Evidence is newer but callable=None → republish_pending (honest pending)."""
        from app.services.intelligence.v3.watchtower_intel_republisher_v1 import (
            PUBLISH_REPUBLISH_PENDING,
            republish_after_analyst_eligibility,
        )

        with patch(
            "app.services.intelligence.v3.watchtower_intel_republisher_v1"
            "._fetch_latest_intel_snapshot",
            return_value={
                "snapshot_id": "snap-old",
                "generated_at": INTEL_SNAPSHOT_AT.isoformat(),
            },
        ):
            result = await republish_after_analyst_eligibility(
                UID,
                MagicMock(),
                intel_republish_callable=None,
                latest_evidence_at=ANALYST_EVIDENCE_AT,
                now=NOW,
            )

        assert result.publish_status == PUBLISH_REPUBLISH_PENDING

    @pytest.mark.asyncio
    async def test_evidence_newer_triggers_republish(self):
        """When analyst evidence is newer than Intel snapshot, republish is triggered."""
        from app.services.intelligence.v3.watchtower_intel_republisher_v1 import (
            PUBLISH_REBUILT_AND_PUBLISHED,
            republish_after_analyst_eligibility,
        )

        mock_republish = AsyncMock(return_value={"snapshot_source": "worker_certified"})

        with patch(
            "app.services.intelligence.v3.watchtower_intel_republisher_v1"
            "._fetch_latest_intel_snapshot",
            return_value={
                "snapshot_id": "snap-old",
                "generated_at": INTEL_SNAPSHOT_AT.isoformat(),
            },
        ):
            result = await republish_after_analyst_eligibility(
                UID,
                MagicMock(),
                intel_republish_callable=mock_republish,
                latest_evidence_at=ANALYST_EVIDENCE_AT,
                now=NOW,
            )

        assert result.publish_status == PUBLISH_REBUILT_AND_PUBLISHED
        assert result.evidence_newer_than_certified_snapshot is True
        mock_republish.assert_called_once_with(UID)


# ── 9. _max_evidence_at helper ────────────────────────────────────────────────

class TestMaxEvidenceAt:

    def _make_record(self, evidence_type: str, as_of: datetime):
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import (
            FRESHNESS_FRESH,
            EvidenceRecord,
        )
        return EvidenceRecord(
            evidence_type=evidence_type,
            ticker="AAPL",
            scope="ticker",
            as_of=as_of,
            collected_at=as_of,
            source="test",
            freshness_status=FRESHNESS_FRESH,
            freshness_sla_seconds=3600,
            deploy_eligible=True,
            decision_eligible=True,
            reason=None,
        )

    def test_returns_max_across_specified_types(self):
        from app.services.intelligence.v3.watchtower_background_refresh_worker_v1 import (
            _max_evidence_at,
        )
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import (
            EVIDENCE_TYPE_ANALYST_LLM,
            EVIDENCE_TYPE_PRICE,
            EVIDENCE_TYPE_RECOMMENDATION,
        )

        t1 = NOW - timedelta(hours=1)
        t2 = NOW - timedelta(minutes=30)
        t3 = NOW - timedelta(minutes=15)

        records = [
            self._make_record(EVIDENCE_TYPE_ANALYST_LLM, t1),
            self._make_record(EVIDENCE_TYPE_RECOMMENDATION, t2),
            self._make_record(EVIDENCE_TYPE_PRICE, t3),  # not in filter set
        ]
        result = _max_evidence_at(
            records,
            evidence_types={EVIDENCE_TYPE_ANALYST_LLM, EVIDENCE_TYPE_RECOMMENDATION},
        )
        assert result == t2, "should return max of analyst+recommendation only, not price"

    def test_returns_none_when_no_records(self):
        from app.services.intelligence.v3.watchtower_background_refresh_worker_v1 import (
            _max_evidence_at,
        )
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import (
            EVIDENCE_TYPE_ANALYST_LLM,
        )

        result = _max_evidence_at([], evidence_types={EVIDENCE_TYPE_ANALYST_LLM})
        assert result is None

    def test_returns_none_when_no_matching_types(self):
        from app.services.intelligence.v3.watchtower_background_refresh_worker_v1 import (
            _max_evidence_at,
        )
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import (
            EVIDENCE_TYPE_ANALYST_LLM,
            EVIDENCE_TYPE_PRICE,
        )

        records = [self._make_record(EVIDENCE_TYPE_PRICE, NOW - timedelta(minutes=5))]
        result = _max_evidence_at(records, evidence_types={EVIDENCE_TYPE_ANALYST_LLM})
        assert result is None

    def test_returns_none_when_evidence_types_is_none_all_null_as_of(self):
        from app.services.intelligence.v3.watchtower_background_refresh_worker_v1 import (
            _max_evidence_at,
        )
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import (
            FRESHNESS_FRESH,
            EvidenceRecord,
            EVIDENCE_TYPE_ANALYST_LLM,
        )

        null_rec = EvidenceRecord(
            evidence_type=EVIDENCE_TYPE_ANALYST_LLM,
            ticker="AAPL",
            scope="ticker",
            as_of=None,
            collected_at=None,
            source="test",
            freshness_status=FRESHNESS_FRESH,
            freshness_sla_seconds=3600,
            deploy_eligible=True,
            decision_eligible=True,
            reason=None,
        )
        result = _max_evidence_at([null_rec])
        assert result is None


# ── 10. Constant exported correctly ──────────────────────────────────────────

class TestConstantExport:

    def test_publish_skipped_no_new_evidence_constant(self):
        from app.services.intelligence.v3.watchtower_intel_republisher_v1 import (
            PUBLISH_SKIPPED_NO_NEW_EVIDENCE,
        )
        assert PUBLISH_SKIPPED_NO_NEW_EVIDENCE == "skipped_no_new_evidence"
