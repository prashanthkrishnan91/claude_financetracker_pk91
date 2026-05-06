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
from .decision_policy_v1 import decide
from .existing_signal_adapter import build_truth_aware_decision_input
from .read_only_evidence_adapter import ReadOnlyEvidenceAdapter
from .portfolio_governor_lite import build_weight_map, compute_portfolio_fit
from .snapshot_builder import build_snapshot
from .source_validator_lite import certify_snapshot_cards, validate_snapshot_cards

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
            # Step 1: load persisted signals via read-only adapter (no legacy aggregation).
            evidence_adapter = ReadOnlyEvidenceAdapter(user_id=self.user_id)
            cards, evidence_stats = await evidence_adapter.load_cards()
            logger.info(
                "intel_v3_evidence_source_summary user_id=%s run_id=%s source_mode=read_only_persisted "
                "active_position_count=%d persisted_recommendation_count=%d persisted_agent_insight_count=%d missing_recommendation_count=%d missing_evidence_count=%d stale_or_missing_source_count=%d "
                "generated_legacy_recommendations=false attempted_llm_calls=0",
                self.user_id, run_id,
                evidence_stats.get("active_position_count", 0),
                evidence_stats.get("persisted_recommendation_count", 0),
                evidence_stats.get("persisted_agent_insight_count", 0),
                evidence_stats.get("missing_recommendation_count", 0),
                evidence_stats.get("missing_evidence_count", 0),
                evidence_stats.get("stale_or_missing_source_count", 0),
            )

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

                analyst_drivers = getattr(card, "analyst_drivers", None)
                if isinstance(analyst_drivers, str):
                    import json
                    try:
                        analyst_drivers = json.loads(analyst_drivers)
                    except Exception:
                        analyst_drivers = []

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
                    # Per-ticker evidence text for visible rationale.
                    primary_driver=getattr(card, "primary_driver", None),
                    risk_flag_text=getattr(card, "risk_flag", None),
                    action_reason=getattr(card, "action_reason", None),
                    analyst_drivers=analyst_drivers,
                    asset_type_hint=category,
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

            # Step 4b: certify cards — fail-closed on hard violations.
            held_cards = snapshot_payload.get("current_holdings", [])
            cert = certify_snapshot_cards(held_cards)

            _results = cert["per_card_results"]
            spam_tickers = cert["spam_tickers"]
            hard_violation_count = cert["hard_violations"]
            soft_violation_count = (
                cert["generic_copy_count"]
                + cert["duplicate_reason_count"]
                + cert["repeated_skeleton_count"]
                + cert["ticker_prefix_only_reason_count"]
                + cert["weak_buy_rationale_count"]
            )

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

            # Soft violations: spam/skeleton warnings — persist with warning.
            skeleton_count = cert["repeated_skeleton_count"]
            prefix_only_count = cert["ticker_prefix_only_reason_count"]
            weak_buy_count = cert["weak_buy_rationale_count"]

            if spam_tickers:
                logger.warning(
                    "intel_v3_run_soft_violation user_id=%s run_id=%s "
                    "spam_tickers=%s",
                    self.user_id, run_id, spam_tickers,
                )
                snapshot_payload["warnings"].append(
                    f"Generic copy detected on {len(spam_tickers)} card(s)."
                )
            if prefix_only_count > 0:
                logger.warning(
                    "intel_v3_run_soft_violation_skeleton user_id=%s run_id=%s "
                    "ticker_prefix_only_reason_count=%d repeated_skeleton_count=%d",
                    self.user_id, run_id, prefix_only_count, skeleton_count,
                )
                snapshot_payload["warnings"].append(
                    f"Ticker-prefix-only rationale detected on {prefix_only_count} card(s). "
                    "Evidence-aware rationale requires primary_driver fields from analyst."
                )

            # Step 5: persist snapshot — only reached when hard_violation_count == 0.
            await self._persist_snapshot(run_id=run_id, payload=snapshot_payload)

            duration_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
            action_counts = snapshot_payload.get("action_counts", {})
            total_cards = len(held_cards)
            snapshot_id = snapshot_payload.get("snapshot_id")

            raw_metric_key_count = cert["raw_metric_key_count"]
            posture_label_count = cert["posture_label_count"]
            conflict_count = cert["action_conflict_count"]

            from collections import Counter as _Counter
            why_texts = [c.get("why_text", "") for c in held_cards if c.get("why_text")]
            why_counts = _Counter(why_texts)
            unique_reason_count = sum(1 for cnt in why_counts.values() if cnt == 1)
            duplicate_reason_count = cert["duplicate_reason_count"]

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
                "generic_copy_count=%d duplicate_reason_count=%d "
                "repeated_skeleton_count=%d ticker_prefix_only_reason_count=%d "
                "weak_buy_rationale_count=%d "
                "action_conflict_count=%d raw_metric_key_count=%d posture_label_count=%d "
                "unique_reason_count=%d "
                "page_load_llm_calls=0 source_path=intel_v3_snapshot "
                "schema_version=%s",
                self.user_id,
                snapshot_id,
                run_id,
                total_cards,
                action_counts,
                soft_violation_count,
                len(spam_tickers),
                duplicate_reason_count,
                skeleton_count,
                prefix_only_count,
                weak_buy_count,
                conflict_count,
                raw_metric_key_count,
                posture_label_count,
                unique_reason_count,
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
