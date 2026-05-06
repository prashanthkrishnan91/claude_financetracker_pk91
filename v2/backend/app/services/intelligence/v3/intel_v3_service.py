"""Intel v3 service — K0-K5 orchestrator for the visible snapshot path.

Replaces the legacy page-load LLM enrichment with:
  1. GET snapshot: reads latest v3 snapshot from DB (zero LLM calls).
  2. POST run:     builds v3 decisions from existing signals + persists snapshot.
  3. GET run:      returns run status / snapshot ID.

Architecture:
  - Uses existing InsightCard data as signal source (no new providers).
  - Runs portfolio governor lite using actual position weights.
  - Runs v3 decision kernel (deterministic, no LLM).
  - Runs source validator to check for contract violations.
  - Persists immutable snapshot to intel_v3_snapshots.

Feature flag: INTEL_V3_VISIBLE_SNAPSHOT_ENABLED
  - If enabled: Intel page reads v3 snapshot only.
  - If disabled: legacy path may remain.

Page-load contract: zero LLM calls, zero provider calls if snapshot exists.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

logger = logging.getLogger(__name__)

from ....database import get_supabase_client
# Transitional input adapter: get_insight_cards() is used as a raw-signal bridge
# only during POST /run. GET /snapshot never calls this path (zero LLM, zero legacy).
from ...recommendation_engine import RecommendationService
from .decision_policy_v1 import decide
from .existing_signal_adapter import build_truth_aware_decision_input
from .portfolio_governor_lite import build_weight_map, compute_portfolio_fit
from .snapshot_builder import build_snapshot
from .source_validator_lite import validate_snapshot_cards

_FLAG_ENV = "INTEL_V3_VISIBLE_SNAPSHOT_ENABLED"
_TRUTHY = {"1", "true", "yes", "on"}


def is_intel_v3_enabled() -> bool:
    return os.getenv(_FLAG_ENV, "").strip().lower() in _TRUTHY


class IntelV3Service:
    """Orchestrates the Intel v3 snapshot read/write pipeline."""

    def __init__(self, user_id: UUID):
        self.user_id = user_id
        self.client = get_supabase_client()

    # ── Snapshot read ─────────────────────────────────────────────────────────

    async def get_latest_snapshot(self) -> Optional[dict[str, Any]]:
        """Read the latest active v3 snapshot for this user.

        Zero LLM calls. Zero provider calls.
        Returns None if no snapshot exists yet.
        Emits intel_v3_snapshot_response_summary log.
        """
        try:
            result = await asyncio.to_thread(
                lambda: self.client.table("intel_v3_snapshots")
                .select("*")
                .eq("user_id", str(self.user_id))
                .eq("is_active", True)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            rows = result.data or []
            if not rows:
                logger.info(
                    "intel_v3_snapshot_response_summary user_id=%s result=no_snapshot",
                    self.user_id,
                )
                return None

            payload = rows[0].get("payload") or {}
            snapshot_id = payload.get("snapshot_id") or rows[0].get("id")
            action_counts = payload.get("action_counts", {})
            total = sum(action_counts.values()) if action_counts else 0

            logger.info(
                "intel_v3_snapshot_response_summary user_id=%s result=found "
                "snapshot_id=%s total_cards=%d action_counts=%s",
                self.user_id,
                snapshot_id,
                total,
                action_counts,
            )
            return payload
        except Exception as exc:
            logger.warning(
                "intel_v3_snapshot_response_summary user_id=%s result=error error=%s",
                self.user_id,
                exc,
            )
            return None

    # ── Run (build decisions + persist snapshot) ──────────────────────────────

    async def run_v3(self) -> dict[str, Any]:
        """Build v3 decisions from existing signals and persist a new snapshot.

        Steps:
          1. Fetch existing InsightCards (uses existing legacy service — data source only).
          2. Fetch portfolio positions for weight-based governor.
          3. For each card: run v3 decision kernel.
          4. Validate snapshot cards.
          5. Build and persist snapshot.
          6. Return snapshot payload.

        Emits intel_v3_snapshot_created log.
        """
        run_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc)

        try:
            # Step 1: get existing signals via InsightCards.
            rec_service = RecommendationService(user_id=self.user_id)
            cards = await rec_service.get_insight_cards()

            # Step 2: get portfolio positions for governor weights.
            weight_map = await self._get_weight_map()

            # Step 3: build decisions for each card.
            decisions = []
            card_metas = []

            for card in cards:
                ticker = card.ticker
                category = card.category or "stock"
                current_pct = weight_map.get(ticker.upper())

                # Build v3 decision input (truth-aware).
                intel_read = getattr(card, "intel_read", None)
                if isinstance(intel_read, str):
                    import json
                    try:
                        intel_read = json.loads(intel_read)
                    except Exception:
                        intel_read = None

                thesis_v2 = getattr(card, "thesis_v2", None)
                if isinstance(thesis_v2, str):
                    import json
                    try:
                        thesis_v2 = json.loads(thesis_v2)
                    except Exception:
                        thesis_v2 = None

                analyst_risks = getattr(card, "analyst_risks", None)
                if isinstance(analyst_risks, str):
                    import json
                    try:
                        analyst_risks = json.loads(analyst_risks)
                    except Exception:
                        analyst_risks = []

                suppression_reasons: dict = {}

                inp, _truth_sums, _suppressed = build_truth_aware_decision_input(
                    ticker=ticker,
                    action=card.action,
                    analyst_action=getattr(card, "analyst_action", None),
                    conviction_level=getattr(card, "conviction_level", None),
                    technical_signal=getattr(card, "technical_signal", None),
                    risk_flag=getattr(card, "risk_flag", None),
                    analyst_risks=analyst_risks,
                    category=category,
                    data_quality_label=getattr(card, "data_quality_label", None),
                    intel_read=intel_read,
                    thesis_v2=thesis_v2,
                    analyst_used_fallback=getattr(card, "analyst_used_fallback", None),
                )

                # Override portfolio_fit with actual weight data if available.
                if current_pct is not None:
                    from .decision_contracts import FitBand
                    fit = compute_portfolio_fit(
                        ticker=ticker,
                        category=category,
                        current_pct=current_pct,
                        suppression_reasons=suppression_reasons,
                    )
                    inp.portfolio_fit = fit

                decision = decide(inp)
                decisions.append(decision)

                card_metas.append({
                    "ticker":      ticker,
                    "name":        card.name or ticker,
                    "category":    category,
                    "thesis_state": "intact",
                })

            # Step 4: build snapshot.
            snapshot_payload = build_snapshot(
                run_id=run_id,
                decisions=decisions,
                card_metas=card_metas,
                source_health={"status": "signals_from_existing_cards"},
                is_stale=False,
            )

            # Step 4b: validate cards — fail-closed on hard violations.
            held_cards = snapshot_payload.get("current_holdings", [])
            _results, spam_tickers, hard_violation_count = validate_snapshot_cards(held_cards)
            soft_violation_count = sum(1 for r in _results if not r.is_valid) - hard_violation_count

            if hard_violation_count > 0:
                # Hard violations: invalid action labels, banned posture labels,
                # action contradictions, raw metric keys, fake price targets.
                # Do NOT persist — raise so the caller can return the error cleanly.
                logger.error(
                    "intel_v3_run_aborted_hard_violations user_id=%s run_id=%s "
                    "hard_violations=%d",
                    self.user_id, run_id, hard_violation_count,
                )
                raise ValueError(
                    f"Intel v3 run aborted: {hard_violation_count} hard validation "
                    f"violation(s) detected. Snapshot not persisted."
                )

            if spam_tickers:
                # Soft violation: generic copy spam — persist with warning.
                logger.warning(
                    "intel_v3_run_soft_violation user_id=%s run_id=%s "
                    "spam_tickers=%s",
                    self.user_id, run_id, spam_tickers,
                )
                snapshot_payload["warnings"].append(
                    f"Generic copy detected on {len(spam_tickers)} card(s)."
                )

            # Step 5: persist snapshot — only reached when hard_violation_count == 0.
            await self._persist_snapshot(run_id=run_id, payload=snapshot_payload)

            duration_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
            action_counts = snapshot_payload.get("action_counts", {})
            total_cards = len(held_cards)
            snapshot_id = snapshot_payload.get("snapshot_id")

            # Count raw metric key violations and posture label violations per card.
            raw_metric_key_count = sum(
                1 for r in _results for v in r.violations if v.rule == "no_raw_metric_keys"
            )
            posture_label_count = sum(
                1 for r in _results for v in r.violations if v.rule == "no_banned_posture_labels"
            )
            conflict_count = sum(
                1 for r in _results for v in r.violations if v.rule == "no_action_contradictions"
            )

            # Count unique vs duplicate why_text across cards.
            why_texts = [c.get("why_text", "") for c in held_cards if c.get("why_text")]
            from collections import Counter as _Counter
            why_counts = _Counter(why_texts)
            unique_reason_count = sum(1 for cnt in why_counts.values() if cnt == 1)
            duplicate_reason_count = sum(cnt for text, cnt in why_counts.items() if cnt > 1)

            logger.info(
                "intel_v3_snapshot_created user_id=%s run_id=%s "
                "snapshot_id=%s total_cards=%d action_counts=%s duration_ms=%d "
                "llm_calls=0 hard_violations=0 soft_violations=%d spam_tickers=%d",
                self.user_id,
                run_id,
                snapshot_id,
                total_cards,
                action_counts,
                duration_ms,
                soft_violation_count,
                len(spam_tickers),
            )

            # Certification summary — validates the v3 snapshot path after every run.
            # Parse from Railway logs using key: intel_v3_snapshot_certification_summary
            logger.info(
                "intel_v3_snapshot_certification_summary "
                "user_id=%s snapshot_id=%s run_id=%s "
                "total_cards=%d action_counts=%s "
                "hard_violations=0 soft_violations=%d "
                "generic_copy_count=%d spam_tickers=%s "
                "raw_metric_key_count=%d posture_label_count=%d conflict_count=%d "
                "unique_reason_count=%d duplicate_reason_count=%d "
                "page_load_llm_calls=0 source_path=intel_v3_snapshot "
                "schema_version=%s",
                self.user_id,
                snapshot_id,
                run_id,
                total_cards,
                action_counts,
                soft_violation_count,
                len(spam_tickers),
                spam_tickers,
                raw_metric_key_count,
                posture_label_count,
                conflict_count,
                unique_reason_count,
                duplicate_reason_count,
                snapshot_payload.get("schema_version", "v3.1"),
            )

            return snapshot_payload

        except Exception as exc:
            logger.error(
                "intel_v3_run_failed user_id=%s run_id=%s error=%s",
                self.user_id, run_id, exc,
            )
            raise

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _get_weight_map(self) -> dict[str, float]:
        """Fetch current positions and build ticker→weight_pct map."""
        try:
            result = await asyncio.to_thread(
                lambda: self.client.table("positions")
                .select("ticker,current_value")
                .eq("user_id", str(self.user_id))
                .execute()
            )
            positions = result.data or []
            return build_weight_map(positions)
        except Exception as exc:
            logger.warning(
                "intel_v3.weight_map_failed user_id=%s error=%s", self.user_id, exc
            )
            return {}

    async def _persist_snapshot(
        self,
        *,
        run_id: str,
        payload: dict[str, Any],
    ) -> None:
        """Persist snapshot to intel_v3_snapshots, deactivating old ones."""
        try:
            # Deactivate previous snapshots.
            await asyncio.to_thread(
                lambda: self.client.table("intel_v3_snapshots")
                .update({"is_active": False})
                .eq("user_id", str(self.user_id))
                .eq("is_active", True)
                .execute()
            )
            # Insert new snapshot.
            source_hash = _hash_payload(payload)
            await asyncio.to_thread(
                lambda: self.client.table("intel_v3_snapshots")
                .insert({
                    "user_id":        str(self.user_id),
                    "run_id":         run_id,
                    "schema_version": payload.get("schema_version", "v3.1"),
                    "payload":        payload,
                    "source_hash":    source_hash,
                    "is_active":      True,
                })
                .execute()
            )
        except Exception as exc:
            logger.warning(
                "intel_v3.persist_snapshot_failed user_id=%s run_id=%s error=%s",
                self.user_id, run_id, exc,
            )
            raise


    async def _get_run_by_id(self, run_id: str) -> Optional[dict[str, Any]]:
        """Look up a snapshot by its run_id."""
        try:
            result = await asyncio.to_thread(
                lambda: self.client.table("intel_v3_snapshots")
                .select("*")
                .eq("user_id", str(self.user_id))
                .eq("run_id", run_id)
                .limit(1)
                .execute()
            )
            rows = result.data or []
            if not rows:
                return None
            payload = rows[0].get("payload") or {}
            return {
                "run_id":      run_id,
                "status":      "completed",
                "snapshot_id": payload.get("snapshot_id"),
                "action_counts": payload.get("action_counts", {}),
                "total_cards": len(payload.get("current_holdings", [])),
                "generated_at": payload.get("generated_at"),
            }
        except Exception as exc:
            logger.warning("intel_v3.get_run_by_id_failed run_id=%s error=%s", run_id, exc)
            return None


def _hash_payload(payload: dict) -> str:
    """Stable hash of snapshot payload for deduplication."""
    import hashlib, json
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
