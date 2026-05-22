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
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

logger = logging.getLogger(__name__)

from ....config import get_settings
from ....database import get_supabase_client
from .decision_policy_v1 import decide
from .existing_signal_adapter import build_truth_aware_decision_input
from .evidence_refresh_orchestrator_v1 import (
    EvidenceRefreshOrchestrator,
    OrchestratorInputs,
    RefreshBudget,
)
from .analyst_refresh_request_seam_v1 import AnalystRefreshRequestSeam
from .read_only_evidence_adapter import ReadOnlyEvidenceAdapter
from .portfolio_governor_lite import build_weight_map, compute_portfolio_fit
from .snapshot_builder import build_snapshot
from .snapshot_freshness_diagnostics import build_diagnostics
from .source_validator_lite import certify_snapshot_cards, validate_snapshot_cards
from .catalyst_display_adapter_v1 import build_catalyst_display_fields as _build_catalyst_display_fields

_FLAG_ENV = "INTEL_V3_VISIBLE_SNAPSHOT_ENABLED"
# Stage 3.1 — analyst refresh-request seam opt-in. Defaults to enabled so the
# synchronous Run Intel v3 path records when stale analyst evidence needs a
# refresh. The seam does NO LLM work — it only records the request. Set to
# "0" / "false" to disable the seam entirely (the run still degrades honestly
# to PARTIAL_CERTIFIED / BLOCKED_UNCERTIFIED on stale analyst evidence).
_ANALYST_REFRESH_FLAG_ENV = "INTEL_V3_ANALYST_REFRESH_ENABLED"
_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


def is_intel_v3_enabled() -> bool:
    return os.getenv(_FLAG_ENV, "").strip().lower() in _TRUTHY


def is_analyst_refresh_enabled() -> bool:
    raw = os.getenv(_ANALYST_REFRESH_FLAG_ENV, "").strip().lower()
    if raw in _FALSY:
        return False
    return True  # default-on; explicit FALSY required to disable


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
        Emits intel_v3_snapshot_response_summary log with snapshot_response_ms.
        """
        _t0 = time.monotonic()
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
            snapshot_response_ms = int((time.monotonic() - _t0) * 1000)
            if not rows:
                logger.info(
                    "intel_v3_snapshot_response_summary user_id=%s result=no_snapshot "
                    "snapshot_response_ms=%d",
                    self.user_id,
                    snapshot_response_ms,
                )
                return None

            payload = rows[0].get("payload") or {}
            snapshot_id = payload.get("snapshot_id") or rows[0].get("id")
            action_counts = payload.get("action_counts", {})
            total = sum(action_counts.values()) if action_counts else 0
            snapshot_source = payload.get("snapshot_source", "unknown")

            # Build 2: embed evidence_freshness_state so callers can show honest
            # state without separate round-trips. Does NOT modify the persisted payload.
            generated_at = payload.get("generated_at")
            try:
                from .watchtower_intel_republisher_v1 import get_evidence_freshness_state
                evidence_freshness_state = await get_evidence_freshness_state(
                    self.user_id,
                    self.client,
                    intel_snapshot_generated_at=generated_at,
                )
            except Exception:
                evidence_freshness_state = "certified_current"

            response_payload = dict(payload)
            response_payload["evidence_freshness_state"] = evidence_freshness_state

            # Response-time normalization: convert legacy committee.status="deferred"
            # to real source-pack status based on serialized evidence_band.
            # Does not mutate the persisted DB row.
            _normalize_legacy_committee_status(
                response_payload,
                user_id=self.user_id,
                snapshot_id=str(snapshot_id),
            )

            logger.info(
                "intel_v3_snapshot_response_summary user_id=%s result=found "
                "snapshot_id=%s total_cards=%d action_counts=%s "
                "snapshot_source=%s evidence_freshness_state=%s snapshot_response_ms=%d",
                self.user_id,
                snapshot_id,
                total,
                action_counts,
                snapshot_source,
                evidence_freshness_state,
                snapshot_response_ms,
            )
            return response_payload
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
          2b. (Phase 11) Optionally fetch SEC metric readiness if adapter enabled.
          3. For each card: run v3 decision kernel.
          4. Validate snapshot cards.
          5. Build and persist snapshot.
          6. Return snapshot payload.

        Emits intel_v3_snapshot_created log.
        """
        run_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc)

        try:
            # Step 0: capture the previous active snapshot for decision-diff diagnostics.
            # Must happen before _persist_snapshot() deactivates it.
            previous_snapshot = await self.get_latest_snapshot()

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

            # Step 1b: Evidence Refresh Orchestrator (Stage 3.0b v1).
            # Classifies per-source freshness, optionally refreshes stale price
            # evidence under deterministic budgets, and produces the run_mode +
            # source_freshness diagnostics that downstream banner/UI consumes.
            # Final decision authority stays with decide(); the orchestrator
            # only refreshes inputs.
            refresh_result = await self._run_refresh_orchestrator(
                run_id=run_id,
                evidence_stats=evidence_stats,
                evidence_adapter=evidence_adapter,
            )

            # Post-refresh re-read guard: if any refresh path persisted a fresh
            # analyst row to durable storage, re-read cards + evidence stats so
            # the deterministic decide() below sees the refreshed rows instead
            # of the pre-refresh snapshot loaded in Step 1.
            #
            # Stage 3.1: the synchronous path wires only the non-LLM analyst
            # refresh-request seam, which never reports successful tickers, so
            # this branch is dormant in-request — it stays as a forward-compat
            # guard for a future background Intelligence Plane that does
            # persist analyst rows. The synchronous request never blocks on it.
            refresh_diag_dict = (
                refresh_result.to_diagnostics_dict() if refresh_result else {}
            )
            analyst_successful = list(
                refresh_diag_dict.get("analyst_refresh_successful_tickers") or []
            )
            if analyst_successful:
                try:
                    cards_post, evidence_stats_post = await evidence_adapter.load_cards()
                    cards = cards_post
                    evidence_stats = evidence_stats_post
                    logger.info(
                        "intel_v3.post_refresh_reread user_id=%s run_id=%s "
                        "refreshed_tickers=%d persisted_recommendation_count=%d "
                        "persisted_agent_insight_count=%d",
                        self.user_id, run_id,
                        len(analyst_successful),
                        evidence_stats.get("persisted_recommendation_count", 0),
                        evidence_stats.get("persisted_agent_insight_count", 0),
                    )
                except Exception as exc:
                    logger.warning(
                        "intel_v3.post_refresh_reread_failed user_id=%s run_id=%s "
                        "err=%s",
                        self.user_id, run_id, exc,
                    )

            # If refresh succeeded for price evidence, the underlying portfolio
            # snapshot still holds yesterday's market_value_certified_at — but
            # the orchestrator already re-classified post-refresh and the
            # diagnostics surface the certified state. The weight map below is
            # built from the latest persisted snapshot; this PR does not yet
            # rewrite the portfolio snapshot itself (avoiding extra DB writes
            # in v1). Read-side weight inputs are unchanged.

            # Step 2: get portfolio positions for governor weights.
            weight_map = await self._get_weight_map()

            # Step 2b: Phase 11 and/or Phase 13 — SEC metric readiness.
            # Fetched once if either adapter is enabled; shared between both phases.
            sec_readiness = await self._get_sec_readiness_for_adapters()

            # Phase 11 governance gate (off by default; governance-gated).
            sec_gate_passed = False
            if sec_readiness is not None:
                settings = get_settings()
                if getattr(settings, "intel_v3_sec_metric_truth_adapter_v1_enabled", False):
                    from .sec_metric_truth_adapter_v1 import check_governance_gate as _p11_gate
                    sec_gate_passed, sec_gate_reason = _p11_gate()
                    if not sec_gate_passed:
                        logger.warning(
                            "intel_v3_phase11_governance_gate_failed user_id=%s run_id=%s reason=%s",
                            self.user_id, run_id, sec_gate_reason,
                        )

            # Phase 13 governance gate (off by default; governance-gated).
            val_gate_passed = False
            settings = get_settings()
            if sec_readiness is not None and getattr(
                settings, "intel_v3_valuation_context_adapter_v1_enabled", False
            ):
                from .valuation_context_adapter_v1 import (
                    check_governance_gate as _p13_gate,
                )
                val_gate_passed, val_gate_reason = _p13_gate()
                if not val_gate_passed:
                    logger.warning(
                        "intel_v3_phase13_governance_gate_failed user_id=%s run_id=%s reason=%s",
                        self.user_id, run_id, val_gate_reason,
                    )

            # Stage 6 — evidence-aware governance shadow (always computed for explanation).
            # Governance mutations are gated on intel_v3_evidence_aware_policy_enabled.
            evidence_shadow = await self._get_evidence_shadow_for_governance(cards)
            settings = get_settings()
            s6_active = (
                evidence_shadow is not None
                and getattr(settings, "intel_v3_evidence_aware_policy_enabled", False)
            )

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

                # Phase 11 — apply SEC fundamentals signal if enabled and gate passed.
                if sec_readiness is not None and sec_gate_passed:
                    from .sec_metric_truth_adapter_v1 import (
                        build_sec_fundamentals_signal,
                        apply_sec_fundamentals_to_decision_input,
                    )
                    sec_signal = build_sec_fundamentals_signal(
                        ticker=ticker.upper(),
                        readiness_result=sec_readiness,
                    )
                    apply_sec_fundamentals_to_decision_input(inp, sec_signal)

                # Phase 13 — apply valuation context signal if enabled and gate passed.
                if sec_readiness is not None and val_gate_passed:
                    from .valuation_context_adapter_v1 import (
                        build_valuation_context_signal,
                        apply_valuation_context_to_decision_input,
                    )
                    has_price = ticker.upper() in weight_map
                    val_signal = build_valuation_context_signal(
                        ticker=ticker.upper(),
                        category=category,
                        sec_readiness=sec_readiness,
                        has_market_price=has_price,
                    )
                    apply_valuation_context_to_decision_input(inp, val_signal)

                # Stage 6 — apply evidence-aware governance (no-op when flag off).
                _gov_result_dict = None
                _research_axis_readiness = None
                if s6_active:
                    from .intel_v3_evidence_aware_governance_v1 import apply_evidence_governance
                    _s6_readiness = evidence_shadow.ticker_readiness.get(ticker.upper())
                    _gov_result = apply_evidence_governance(
                        inp,
                        _s6_readiness,
                        evidence_shadow.portfolio_macro,
                        flag_enabled=True,
                    )
                    _gov_result_dict = _gov_result.to_dict()
                    # Stage 8D: catalyst display available even on the Stage 6 active path.
                    # Governance result drives decisions; this is display-only metadata.
                    if _s6_readiness is not None:
                        _research_axis_readiness = {
                            "sec_catalyst_display": _build_catalyst_display_fields(_s6_readiness),
                        }
                elif evidence_shadow is not None:
                    # Stage 6 inactive but shadow available: populate axis readiness for
                    # evidence_explanation (technical_signals_status, sentiment_status).
                    # No decision inputs are mutated.
                    _s6_readiness = evidence_shadow.ticker_readiness.get(ticker.upper())
                    if _s6_readiness is not None:
                        _tech_axis = _s6_readiness.axes.get("technical_signals")
                        _sent_axis = _s6_readiness.axes.get("sentiment")
                        _research_axis_readiness = {
                            "technical_signals": _tech_axis.readiness if _tech_axis else "MISSING",
                            "sentiment": _sent_axis.readiness if _sent_axis else "MISSING",
                        }
                        if _sent_axis is not None and _sent_axis.is_usable:
                            _sent_source = (
                                "sec_catalyst_sentiment"
                                if "sec_catalyst_sentiment" in (_sent_axis.contributing_lanes or [])
                                else "news_sentiment"
                            )
                            logger.info(
                                "snapshot_sentiment_readiness ticker=%s status=%s source=%s",
                                ticker.upper(),
                                _sent_axis.readiness,
                                _sent_source,
                            )
                        # Stage 8D: safe catalyst display fields for UI (no raw codes).
                        _research_axis_readiness["sec_catalyst_display"] = (
                            _build_catalyst_display_fields(_s6_readiness)
                        )

                decision = decide(inp)
                decisions.append(decision)

                card_metas.append({
                    "ticker":             ticker,
                    "name":               card.name or ticker,
                    "category":           category,
                    "thesis_state":       "intact",
                    "governance_result":  _gov_result_dict,
                    "research_axis_readiness": _research_axis_readiness,
                })

            # Step 3b: Build valuation context map when flag enabled (Build 3 PR 2B).
            valuation_context_map = await self._build_valuation_context_map(cards)

            # Step 4: build snapshot (without diagnostics initially; diagnostics need the payload).
            snapshot_payload = build_snapshot(
                run_id=run_id,
                decisions=decisions,
                card_metas=card_metas,
                source_health={"status": "signals_from_existing_cards"},
                is_stale=False,
                valuation_context_map=valuation_context_map,
            )

            # Step 4a: compute freshness + decision-diff diagnostics and embed.
            refresh_diag = (
                refresh_result.to_diagnostics_dict() if refresh_result else None
            )
            diagnostics = build_diagnostics(
                evidence_stats=evidence_stats,
                current_snapshot=snapshot_payload,
                previous_snapshot=previous_snapshot,
                refresh_diagnostics=refresh_diag,
            )
            snapshot_payload["diagnostics"] = diagnostics

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
            # Full list of tickers with ticker-prefix-only rationale — used for
            # rationale repair enqueue below (fresh-but-uncertifiable recovery).
            _prefix_only_tickers: list[str] = cert.get("ticker_prefix_spam_tickers", [])

            if prefix_only_count > 0:
                prefix_examples = cert.get("examples", {}).get("ticker_prefix_only")
                skeleton_examples = cert.get("examples", {}).get("repeated_skeleton")
                logger.warning(
                    "intel_v3_run_soft_violation_skeleton user_id=%s run_id=%s "
                    "ticker_prefix_only_reason_count=%d repeated_skeleton_count=%d "
                    "ticker_prefix_only_examples=%s repeated_skeleton_examples=%s",
                    self.user_id, run_id, prefix_only_count, skeleton_count,
                    prefix_examples, skeleton_examples,
                )
                snapshot_payload["warnings"].append(
                    f"Ticker-prefix-only rationale detected on {prefix_only_count} card(s). "
                    "Evidence-aware rationale requires primary_driver fields from analyst."
                )

            # Step 5: persist snapshot — only reached when hard_violation_count == 0.
            await self._persist_snapshot(run_id=run_id, payload=snapshot_payload)

            # Step 5b: enqueue rationale repair jobs when ticker-prefix-only rationale
            # is detected. Evidence timestamps are "fresh" so stale-age detection never
            # fires for these tickers, but the analyst_verdict rows are uncertifiable
            # (primary_driver/action_reason/risk_flag missing — written by PR #319's
            # lossy writeback). Enqueueing here forces the worker to rewrite them using
            # the PR #320 structured-verdict path on next poll.
            if _prefix_only_tickers:
                await self._enqueue_rationale_repair(
                    run_id=run_id,
                    tickers=_prefix_only_tickers,
                    started_at=started_at,
                )

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

            # Freshness/diff summary — parse from Railway logs using key: intel_v3_freshness_summary
            _diag = diagnostics
            logger.info(
                "intel_v3_freshness_summary user_id=%s snapshot_id=%s run_id=%s "
                "evidence_mode=%s run_mode=%s trust_status=%s action_counts=%s "
                "max_recommendation_age_hours=%s max_agent_insight_age_hours=%s "
                "stale_evidence_count=%d missing_evidence_count=%d "
                "changed_decision_count=%d previous_snapshot_id=%s "
                "attempted_provider_calls=%d successful_provider_calls=%d failed_provider_calls=%d "
                "attempted_llm_calls=%d successful_llm_calls=%d failed_llm_calls=%d "
                "refreshed_source_count=%d failed_refresh_count=%d "
                "analyst_refresh_supported=%s analyst_refresh_status=%s "
                "budget_exhausted=%s",
                self.user_id,
                snapshot_id,
                run_id,
                _diag.get("evidence_mode"),
                _diag.get("run_mode"),
                _diag.get("trust_status"),
                action_counts,
                _diag.get("max_recommendation_age_hours"),
                _diag.get("max_agent_insight_age_hours"),
                _diag.get("stale_evidence_count", 0),
                _diag.get("missing_evidence_count", 0),
                _diag.get("changed_decision_count", 0),
                _diag.get("previous_snapshot_id"),
                _diag.get("attempted_provider_calls", 0),
                _diag.get("successful_provider_calls", 0),
                _diag.get("failed_provider_calls", 0),
                _diag.get("attempted_llm_calls", 0),
                _diag.get("successful_llm_calls", 0),
                _diag.get("failed_llm_calls", 0),
                _diag.get("refreshed_source_count", 0),
                _diag.get("failed_refresh_count", 0),
                _diag.get("analyst_refresh_supported", False),
                _diag.get("analyst_refresh_status", "not_attempted"),
                _diag.get("budget_exhausted", False),
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

            # Evidence-depth aggregate summary — shared helper, same log key as prewarm path.
            _log_evidence_depth_summary(
                user_id=self.user_id,
                snapshot_id=snapshot_id,
                run_id=run_id,
                snapshot_payload=snapshot_payload,
                decisions=decisions,
                evidence_stats=evidence_stats,
            )

            return snapshot_payload

        except Exception as exc:
            logger.error(
                "intel_v3_run_failed user_id=%s run_id=%s error=%s",
                self.user_id, run_id, exc,
            )
            raise

    # ── Run Intel v3 enqueue (Stage 3.3 — all-or-nothing contract) ──────────

    async def enqueue_run_v3(self) -> dict[str, Any]:
        """Enqueue a full analyst refresh for all active holdings.

        This is the only action the Run Intel v3 button performs:
          1. Fetches all active tickers.
          2. Enqueues a durable ``analyst_refresh_jobs`` row per ticker via
             ``enqueue_refresh_jobs`` — idempotent, fast, no LLM work.
          3. Returns a status dict that the UI uses to show a "refresh in
             progress" state while the background worker processes the jobs.

        Explicitly does NOT:
          * Build or return a snapshot.
          * Run any analyst / LLM work in-request.
          * Mark any evidence as fresh or green.

        After this call the UI should:
          * Show "Refreshing Analyst Intelligence" (or "Latest Certified
            Snapshot Available — New Refresh Running" if a certified snapshot
            already exists).
          * Poll GET /intel/v3/snapshot until ``snapshot_source=worker_certified``
            appears, then show the certified state.

        Emits ``intel_v3_run_request_received`` and ``intel_v3_full_refresh_enqueued``.
        """
        import asyncio as _asyncio
        from .analyst_refresh_job_store_v1 import enqueue_refresh_jobs

        started_at = datetime.now(timezone.utc)

        logger.info(
            "intel_v3_run_request_received user_id=%s",
            self.user_id,
        )

        # Fetch active tickers
        tickers = await self._get_active_tickers()
        total_holding_count = len(tickers)

        if not tickers:
            logger.warning(
                "intel_v3_full_refresh_enqueued user_id=%s "
                "status=no_active_holdings queued_ticker_count=0",
                self.user_id,
            )
            return {
                "status": "no_active_holdings",
                "queued_ticker_count": 0,
                "total_holding_count": 0,
                "existing_certified_snapshot_id": None,
                "existing_certified_snapshot": False,
                "message": "No active holdings found. Add positions before running Intel v3.",
            }

        # Check whether a certified snapshot already exists (to guide UI copy)
        latest_snapshot = await self.get_latest_snapshot()
        existing_certified = (
            isinstance(latest_snapshot, dict)
            and latest_snapshot.get("snapshot_source") == "worker_certified"
        )
        existing_certified_snapshot_id = (
            latest_snapshot.get("snapshot_id") if latest_snapshot else None
        )

        # Fast freshness gate runs FIRST — determines which analyst slices are
        # actually stale. Only those tickers get enqueued. This prevents the
        # Run Intel button from blindly reopening all 34 analyst jobs on every
        # click when analyst evidence is already fresh.
        freshness_gate_summary: dict[str, Any] = {}
        stale_analyst_tickers: list[str] = list(tickers)  # safe fallback: all
        gate_succeeded = False
        try:
            from .intel_v3_fast_freshness_gate_v1 import run_fast_freshness_gate
            gate_result = await run_fast_freshness_gate(
                self.user_id,
                self.client,
                now=started_at,
                existing_certified_snapshot_id=existing_certified_snapshot_id,
                has_pending_worker_jobs=False,  # unknown pre-enqueue; set after
                total_holdings=total_holding_count,
            )
            stale_analyst_tickers = _stale_analyst_tickers_from_gate(gate_result)
            gate_succeeded = True
            freshness_gate_summary = {
                "intel_status":                  gate_result.intel_status,
                "deploy_status":                 gate_result.deploy_status,
                "deploy_blockers":               gate_result.deploy_blockers,
                "urgent_refresh_count":          gate_result.refresh_plan.urgent_refresh_count,
                "gate_check_ms":                 gate_result.gate_check_ms,
                "stale_analyst_tickers_count":   len(stale_analyst_tickers),
                "total_holding_count":           total_holding_count,
            }
        except Exception as _gate_exc:
            logger.warning(
                "intel_v3_fast_freshness_gate_failed user_id=%s error=%s — "
                "falling back to full-ticker enqueue",
                self.user_id, _gate_exc,
            )
            # gate_succeeded remains False; stale_analyst_tickers = all tickers

        # When gate shows price/weight is stale, fire an urgent Watchtower price refresh
        # as a fire-and-forget background task. This does not block the enqueue response.
        urgent_refresh_triggered = False
        if gate_succeeded:
            deploy_blockers = getattr(
                getattr(gate_result, "refresh_plan", None), "deploy_blockers", ()
            ) or gate_result.deploy_blockers
            price_weight_stale = any(
                b in ("price", "portfolio_weight") for b in (deploy_blockers or [])
            )
            if price_weight_stale:
                try:
                    from .watchtower_background_refresh_worker_v1 import (
                        run_watchtower_cycle_for_user,
                    )
                    from .watchtower_callables_v1 import (
                        build_default_price_refresh_callable,
                        build_default_analyst_enqueue_callable,
                        build_default_intel_republish_callable,
                    )
                    _asyncio.create_task(
                        run_watchtower_cycle_for_user(
                            self.user_id,
                            self.client,
                            price_refresh_callable=build_default_price_refresh_callable(
                                self.client
                            ),
                            analyst_job_enqueue_callable=build_default_analyst_enqueue_callable(
                                self.client
                            ),
                            intel_republish_callable=build_default_intel_republish_callable(
                                self.client
                            ),
                        )
                    )
                    urgent_refresh_triggered = True
                    logger.info(
                        "intel_v3_urgent_watchtower_refresh_triggered user_id=%s "
                        "deploy_blockers=%s",
                        self.user_id, list(deploy_blockers or []),
                    )
                except Exception as _wt_exc:
                    logger.warning(
                        "intel_v3_urgent_watchtower_refresh_trigger_failed user_id=%s "
                        "error=%s",
                        self.user_id, _wt_exc,
                    )

        # Enqueue analyst refresh only for tickers with stale/missing analyst evidence.
        # When gate succeeded and no tickers are stale, queued_count=0 (refresh not needed).
        # When gate failed, we fall back to enqueuing all tickers (safe degradation).
        enqueue_tickers = stale_analyst_tickers
        queued_count = 0
        enqueue_result_created = 0
        enqueue_result_touched = 0
        enqueue_result_made_due = 0
        enqueue_result_reopened = 0

        if enqueue_tickers:
            try:
                enqueue_result = await _asyncio.to_thread(
                    enqueue_refresh_jobs,
                    self.client,
                    user_id=self.user_id,
                    tickers=enqueue_tickers,
                    now=started_at,
                )
                queued_count = (
                    enqueue_result.created_count
                    + enqueue_result.touched_count
                    + enqueue_result.made_due_count
                    + enqueue_result.reopened_count
                )
                enqueue_result_created = enqueue_result.created_count
                enqueue_result_touched = enqueue_result.touched_count
                enqueue_result_made_due = enqueue_result.made_due_count
                enqueue_result_reopened = enqueue_result.reopened_count
            except Exception as exc:
                logger.error(
                    "intel_v3_full_refresh_enqueued user_id=%s "
                    "status=enqueue_failed error=%s",
                    self.user_id, exc,
                )
                return {
                    "status": "enqueue_failed",
                    "queued_ticker_count": 0,
                    "total_holding_count": total_holding_count,
                    "existing_certified_snapshot_id": existing_certified_snapshot_id,
                    "existing_certified_snapshot": existing_certified,
                    "freshness_gate": freshness_gate_summary,
                    "message": f"Failed to enqueue refresh: {exc}",
                }

        # Determine status. When gate determined analyst evidence is current
        # (0 stale tickers), also check evidence mapping version. A stale mapping
        # version means the persisted snapshot was built before PR #347's synthesis
        # change and must be recertified deterministically before reporting current.
        if gate_succeeded and not stale_analyst_tickers:
            from .evidence_mapping_version_v1 import (
                EVIDENCE_MAPPING_VERSION as _CURRENT_MAPPING_VER,
                is_snapshot_mapping_current as _is_mapping_current,
            )
            from .stage7_snapshot_contract_v1 import (
                STAGE7_EXPLANATION_CONTRACT_VERSION as _CURRENT_STAGE7_VER,
                is_snapshot_stage7_complete as _is_stage7_complete,
            )
            _snap_mapping_ver = (
                latest_snapshot.get("evidence_mapping_version") if latest_snapshot else None
            )
            _mapping_current = _is_mapping_current(latest_snapshot or {})
            _snap_stage7_ver = (
                latest_snapshot.get("stage7_explanation_contract_version") if latest_snapshot else None
            )
            # Use is_snapshot_stage7_complete: checks version marker AND card explanation keys.
            _stage7_current = _is_stage7_complete(latest_snapshot or {})
            logger.info(
                "intel_v3_evidence_mapping_version_summary user_id=%s "
                "current_evidence_mapping_version=%s "
                "latest_snapshot_evidence_mapping_version=%s "
                "mapping_version_current=%s "
                "stage7_explanation_contract_version=%s "
                "latest_snapshot_stage7_contract_version=%s "
                "stage7_contract_current=%s "
                "deterministic_republish_required=%s "
                "analyst_jobs_required=false "
                "snapshot_id=%s",
                self.user_id,
                _CURRENT_MAPPING_VER,
                _snap_mapping_ver or "missing",
                _mapping_current,
                _CURRENT_STAGE7_VER,
                _snap_stage7_ver or "missing",
                _stage7_current,
                not _mapping_current or not _stage7_current,
                existing_certified_snapshot_id or "none",
            )
            if not _mapping_current:
                # Mapping version stale — trigger zero-LLM deterministic recertification.
                try:
                    _prewarm_id = str(uuid.uuid4())
                    await self.run_prewarm_snapshot(prewarm_run_id=_prewarm_id)
                    status = "mapping_version_recertified"
                except Exception as _prewarm_exc:
                    logger.warning(
                        "intel_v3_evidence_mapping_version_recertification_failed "
                        "user_id=%s error=%s",
                        self.user_id, _prewarm_exc,
                    )
                    status = "mapping_version_recertification_failed"
            elif not _stage7_current:
                # Stage 7 explanation contract missing — trigger zero-LLM deterministic
                # recertification. No analyst jobs enqueued; only snapshot payload is rebuilt
                # using existing Stage 6 governance outputs and the Stage 7 explanation path.
                try:
                    _prewarm_id = str(uuid.uuid4())
                    await self.run_prewarm_snapshot(prewarm_run_id=_prewarm_id)
                    status = "stage7_contract_recertified"
                except Exception as _prewarm_exc:
                    logger.warning(
                        "intel_v3_stage7_contract_recertification_failed "
                        "user_id=%s error=%s",
                        self.user_id, _prewarm_exc,
                    )
                    status = "stage7_contract_recertification_failed"
            else:
                status = "analyst_evidence_current"
        elif enqueue_result_touched > 0 and enqueue_result_created == 0:
            status = "refresh_in_progress"
        else:
            status = "refresh_requested"

        run_click_response_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
        refresh_jobs_pending_count = queued_count
        refresh_jobs_remaining_count = queued_count

        logger.info(
            "intel_v3_full_refresh_enqueued user_id=%s "
            "status=%s queued_ticker_count=%d stale_analyst_count=%d total_holding_count=%d "
            "created=%d touched=%d made_due=%d reopened=%d "
            "existing_certified_snapshot=%s existing_certified_snapshot_id=%s "
            "run_click_response_ms=%d certified_snapshot_available_on_click=%s "
            "refresh_jobs_pending_count=%d gate_succeeded=%s",
            self.user_id,
            status,
            queued_count,
            len(stale_analyst_tickers),
            total_holding_count,
            enqueue_result_created,
            enqueue_result_touched,
            enqueue_result_made_due,
            enqueue_result_reopened,
            existing_certified,
            existing_certified_snapshot_id,
            run_click_response_ms,
            existing_certified,
            refresh_jobs_pending_count,
            gate_succeeded,
        )

        # Update gate summary with post-enqueue state
        if freshness_gate_summary:
            freshness_gate_summary["has_pending_worker_jobs"] = (queued_count > 0)

        # Dispatch enabled evidence lanes for all portfolio tickers on explicit run.
        # Fires regardless of analyst freshness — evidence lane population is independent
        # of the analyst refresh cycle. Fire-and-forget; does not delay the 202 response.
        # Only reachable from explicit POST /run, not page-load GET.
        from .intel_v3_evidence_lane_orchestrator_v1 import (
            run_enabled_evidence_lanes_for_portfolio,
        )
        _evidence_run_id = str(uuid.uuid4())
        _evidence_user_id = str(self.user_id)
        _evidence_tickers = list(tickers)
        _evidence_client = self.client
        _evidence_settings = get_settings()
        # Stage 5H.3 patch — fetch best-effort holding_context (category) so
        # the SEC CompanyFacts non-equity guard can decide eligibility from
        # actual position metadata when available, not just the static
        # BTC/XRP/ETF symbol fallback. Empty dict on any DB failure is safe.
        _evidence_holding_context_by_ticker = (
            await self._get_active_holding_context_by_ticker()
        )

        # Log before scheduling so Railway can confirm dispatch was attempted even if
        # the background thread fails or the process terminates before it completes.
        logger.info(
            "intel_v3_evidence_lanes_dispatch_scheduled user_id=%s "
            "total_tickers=%d parent_intel_run_id=%s",
            _evidence_user_id,
            len(_evidence_tickers),
            _evidence_run_id,
        )

        # Build the post-lane republish callable here — same callable used by Watchtower.
        # Captured before the background task runs to avoid any request-lifetime concerns.
        from .watchtower_callables_v1 import build_default_intel_republish_callable as _build_republish
        _post_lane_republish_callable = _build_republish(_evidence_client)

        async def _run_evidence_lanes_safe() -> None:
            try:
                await _asyncio.to_thread(
                    run_enabled_evidence_lanes_for_portfolio,
                    _evidence_user_id,
                    _evidence_tickers,
                    _evidence_client,
                    _evidence_run_id,
                    _evidence_settings,
                    _evidence_holding_context_by_ticker,
                )
            except Exception as _exc:
                logger.warning(
                    "intel_v3_evidence_lanes_dispatch_failed user_id=%s "
                    "parent_intel_run_id=%s error=%s",
                    _evidence_user_id,
                    _evidence_run_id,
                    _exc,
                )
                return  # do not attempt republish if lanes failed

            # Stage 8A.3 — Post-lane completion republish.
            # If any usable technical artifact written by the lanes is newer than the
            # active certified snapshot, trigger deterministic snapshot rebuild. This
            # closes the timing gap where evidence lanes complete async after the 202
            # response but Watchtower (which uses portfolio_snapshots timestamps) has
            # no visibility into research_artifacts timestamps.
            try:
                from .watchtower_intel_republisher_v1 import (
                    compare_and_republish_after_evidence_lanes as _post_lane_republish,
                )
                await _post_lane_republish(
                    _evidence_user_id,
                    _evidence_client,
                    intel_republish_callable=_post_lane_republish_callable,
                )
            except Exception as _repr_exc:
                logger.warning(
                    "intel_v3_post_lane_republish_failed user_id=%s "
                    "parent_intel_run_id=%s error=%s",
                    _evidence_user_id,
                    _evidence_run_id,
                    _repr_exc,
                )

        _asyncio.create_task(_run_evidence_lanes_safe())

        return {
            "status": status,
            "queued_ticker_count": queued_count,
            "stale_analyst_ticker_count": len(stale_analyst_tickers),
            "total_holding_count": total_holding_count,
            "existing_certified_snapshot_id": existing_certified_snapshot_id,
            "existing_certified_snapshot": existing_certified,
            "run_click_response_ms": run_click_response_ms,
            "certified_snapshot_available_on_click": existing_certified,
            "refresh_jobs_pending_count": refresh_jobs_pending_count,
            "refresh_jobs_remaining_count": refresh_jobs_remaining_count,
            "freshness_gate": freshness_gate_summary,
            "urgent_refresh_triggered": urgent_refresh_triggered,
            "message": (
                f"Analyst refresh enqueued for {queued_count}/{total_holding_count} holdings. "
                "Background worker will run LLM analysis and publish a certified snapshot."
                if queued_count > 0
                else "Deterministic recertification failed — evidence mapping version mismatch. "
                "Retry Run Intel to recertify."
                if status == "mapping_version_recertification_failed"
                else "Deterministic recertification failed — Stage 7 explanation contract missing. "
                "Retry Run Intel to recertify."
                if status == "stage7_contract_recertification_failed"
                else "Analyst evidence is current — no refresh needed."
            ),
        }

    # ── Deterministic prewarm (Stage 3.2c) ───────────────────────────────────

    async def run_prewarm_snapshot(self, *, prewarm_run_id: str, skip_persist_on_fail: bool = False) -> dict[str, Any]:
        """Build and persist a snapshot from current persisted evidence. Zero LLM calls.

        Mirrors the decision-build + persist steps of ``run_v3()`` but intentionally
        skips ``_run_refresh_orchestrator``.  This means:
          * No ``AnalystRefreshRequestSeam`` call → no ``analyst_refresh_jobs`` rows
            inserted → no recursive worker trigger.
          * No price-refresh provider calls.
          * Only reads ``agent_insights`` / ``recommendations`` / ``positions`` and
            runs the deterministic ``decide()`` kernel.

        Emits structured logs:
          analyst_refresh_snapshot_prewarm_started / _completed / _failed
          (emitted by ``_trigger_snapshot_prewarm`` in the caller)
          intel_v3_snapshot_created (standard snapshot log, with run_id=prewarm_run_id)
        """
        started_at = datetime.now(timezone.utc)

        # Step 0: capture previous snapshot for decision-diff diagnostics.
        previous_snapshot = await self.get_latest_snapshot()

        # Step 1: load evidence — same read-only adapter as run_v3().
        evidence_adapter = ReadOnlyEvidenceAdapter(user_id=self.user_id)
        cards, evidence_stats = await evidence_adapter.load_cards()
        logger.info(
            "intel_v3_prewarm_evidence_source user_id=%s prewarm_run_id=%s "
            "active_position_count=%d persisted_recommendation_count=%d "
            "persisted_agent_insight_count=%d missing_evidence_count=%d",
            self.user_id, prewarm_run_id,
            evidence_stats.get("active_position_count", 0),
            evidence_stats.get("persisted_recommendation_count", 0),
            evidence_stats.get("persisted_agent_insight_count", 0),
            evidence_stats.get("missing_evidence_count", 0),
        )

        # Step 2: portfolio weight map.
        weight_map = await self._get_weight_map()

        # Step 2b: SEC readiness (governance-gated; reuses same adapters as run_v3).
        sec_readiness = await self._get_sec_readiness_for_adapters()

        sec_gate_passed = False
        if sec_readiness is not None:
            settings = get_settings()
            if getattr(settings, "intel_v3_sec_metric_truth_adapter_v1_enabled", False):
                from .sec_metric_truth_adapter_v1 import check_governance_gate as _p11_gate
                sec_gate_passed, _ = _p11_gate()

        val_gate_passed = False
        settings = get_settings()
        if sec_readiness is not None and getattr(
            settings, "intel_v3_valuation_context_adapter_v1_enabled", False
        ):
            from .valuation_context_adapter_v1 import check_governance_gate as _p13_gate
            val_gate_passed, _ = _p13_gate()

        # Stage 6 — evidence-aware governance shadow (always computed for explanation).
        # Governance mutations are gated on intel_v3_evidence_aware_policy_enabled.
        evidence_shadow = await self._get_evidence_shadow_for_governance(cards)
        settings = get_settings()
        s6_active = (
            evidence_shadow is not None
            and getattr(settings, "intel_v3_evidence_aware_policy_enabled", False)
        )

        # Step 3: build decisions (identical to run_v3 card loop).
        decisions = []
        card_metas = []

        for card in cards:
            ticker = card.ticker
            category = card.category or "stock"
            current_pct = weight_map.get(ticker.upper())

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
                primary_driver=getattr(card, "primary_driver", None),
                risk_flag_text=getattr(card, "risk_flag", None),
                action_reason=getattr(card, "action_reason", None),
                analyst_drivers=analyst_drivers,
                asset_type_hint=category,
            )

            if current_pct is not None:
                fit = compute_portfolio_fit(
                    ticker=ticker,
                    category=category,
                    current_pct=current_pct,
                    suppression_reasons=suppression_reasons,
                )
                inp.portfolio_fit = fit

            if sec_readiness is not None and sec_gate_passed:
                from .sec_metric_truth_adapter_v1 import (
                    build_sec_fundamentals_signal,
                    apply_sec_fundamentals_to_decision_input,
                )
                sec_signal = build_sec_fundamentals_signal(
                    ticker=ticker.upper(), readiness_result=sec_readiness,
                )
                apply_sec_fundamentals_to_decision_input(inp, sec_signal)

            if sec_readiness is not None and val_gate_passed:
                from .valuation_context_adapter_v1 import (
                    build_valuation_context_signal,
                    apply_valuation_context_to_decision_input,
                )
                val_signal = build_valuation_context_signal(
                    ticker=ticker.upper(),
                    category=category,
                    sec_readiness=sec_readiness,
                    has_market_price=ticker.upper() in weight_map,
                )
                apply_valuation_context_to_decision_input(inp, val_signal)

            # Stage 6 — apply evidence-aware governance (no-op when flag off).
            _gov_result_dict = None
            _research_axis_readiness = None
            if s6_active:
                from .intel_v3_evidence_aware_governance_v1 import apply_evidence_governance
                _s6_readiness = evidence_shadow.ticker_readiness.get(ticker.upper())
                _gov_result = apply_evidence_governance(
                    inp,
                    _s6_readiness,
                    evidence_shadow.portfolio_macro,
                    flag_enabled=True,
                )
                _gov_result_dict = _gov_result.to_dict()
                # Stage 8D: catalyst display available even on the Stage 6 active path.
                if _s6_readiness is not None:
                    _research_axis_readiness = {
                        "sec_catalyst_display": _build_catalyst_display_fields(_s6_readiness),
                    }
            elif evidence_shadow is not None:
                # Stage 6 inactive but shadow available: populate axis readiness for
                # evidence_explanation (technical_signals_status, sentiment_status).
                # No decision inputs are mutated.
                _s6_readiness = evidence_shadow.ticker_readiness.get(ticker.upper())
                if _s6_readiness is not None:
                    _tech_axis = _s6_readiness.axes.get("technical_signals")
                    _sent_axis = _s6_readiness.axes.get("sentiment")
                    _research_axis_readiness = {
                        "technical_signals": _tech_axis.readiness if _tech_axis else "MISSING",
                        "sentiment": _sent_axis.readiness if _sent_axis else "MISSING",
                    }
                    if _sent_axis is not None and _sent_axis.is_usable:
                        _sent_source = (
                            "sec_catalyst_sentiment"
                            if "sec_catalyst_sentiment" in (_sent_axis.contributing_lanes or [])
                            else "news_sentiment"
                        )
                        logger.info(
                            "snapshot_sentiment_readiness ticker=%s status=%s source=%s",
                            ticker.upper(),
                            _sent_axis.readiness,
                            _sent_source,
                        )
                    # Stage 8D: safe catalyst display fields for UI (no raw codes).
                    _research_axis_readiness["sec_catalyst_display"] = (
                        _build_catalyst_display_fields(_s6_readiness)
                    )

            decision = decide(inp)
            decisions.append(decision)
            card_metas.append({
                "ticker":             ticker,
                "name":               card.name or ticker,
                "category":           category,
                "thesis_state":       "intact",
                "governance_result":  _gov_result_dict,
                "research_axis_readiness": _research_axis_readiness,
            })

        # Step 3b: Build valuation context map when flag enabled (Build 3 PR 2B).
        valuation_context_map = await self._build_valuation_context_map(cards)

        # Step 4: build snapshot.
        snapshot_payload = build_snapshot(
            run_id=prewarm_run_id,
            decisions=decisions,
            card_metas=card_metas,
            source_health={"status": "signals_from_existing_cards"},
            is_stale=False,
            valuation_context_map=valuation_context_map,
        )

        # Step 4a: diagnostics.
        diagnostics = build_diagnostics(
            evidence_stats=evidence_stats,
            current_snapshot=snapshot_payload,
            previous_snapshot=previous_snapshot,
            refresh_diagnostics=None,
        )
        snapshot_payload["diagnostics"] = diagnostics

        # Step 4b: certify cards — fail-closed on hard violations.
        held_cards = snapshot_payload.get("current_holdings", [])
        cert = certify_snapshot_cards(held_cards)
        hard_violation_count = cert["hard_violations"]
        prefix_only_count = cert["ticker_prefix_only_reason_count"]
        soft_violation_count = (
            cert["generic_copy_count"]
            + cert["duplicate_reason_count"]
            + cert["repeated_skeleton_count"]
            + prefix_only_count
            + cert["weak_buy_rationale_count"]
        )

        if hard_violation_count > 0:
            logger.error(
                "intel_v3_prewarm_aborted_hard_violations user_id=%s prewarm_run_id=%s "
                "hard_violations=%d",
                self.user_id, prewarm_run_id, hard_violation_count,
            )
            raise ValueError(
                f"Intel v3 prewarm aborted: {hard_violation_count} hard violation(s). "
                "Snapshot not persisted."
            )

        if prefix_only_count > 0:
            logger.warning(
                "intel_v3_prewarm_soft_violation_skeleton user_id=%s prewarm_run_id=%s "
                "ticker_prefix_only_reason_count=%d",
                self.user_id, prewarm_run_id, prefix_only_count,
            )
            snapshot_payload["warnings"].append(
                f"Ticker-prefix-only rationale detected on {prefix_only_count} card(s). "
                "Evidence-aware rationale requires primary_driver fields from analyst."
            )

        # Step 5: run the all-or-nothing CertifiedIntelRunContract (Stage 3.3).
        # A prewarm snapshot is only published as "worker_certified" when every
        # active holding passes the full evidence contract. Partial coverage,
        # stale evidence, template rationale, or DB read failures produce a
        # "certification_failed" snapshot — persisted for honest UI display but
        # never shown as green.
        from .certified_intel_run_contract_v1 import check_certified_intel_run_contract
        try:
            contract = await check_certified_intel_run_contract(
                user_id=self.user_id,
                client=self.client,
                now=started_at,
            )
        except Exception as exc:
            logger.error(
                "intel_v3_prewarm_contract_check_failed user_id=%s prewarm_run_id=%s err=%s",
                self.user_id, prewarm_run_id, exc,
            )
            contract = None

        contract_dict = contract.to_dict() if contract else {}
        contract_certified = bool(contract and contract.certified)

        if contract_certified:
            snapshot_source = "worker_certified"
            logger.info(
                "intel_v3_worker_certified_snapshot_published user_id=%s "
                "prewarm_run_id=%s snapshot_id=%s "
                "total_holding_count=%d certified_holding_count=%d "
                "failed_holding_count=0 failed_tickers=none "
                "latest_agent_run_at=%s latest_recommendation_at=%s "
                "agent_run_ids=%s",
                self.user_id,
                prewarm_run_id,
                snapshot_payload.get("snapshot_id"),
                contract_dict.get("total_holding_count", 0),
                contract_dict.get("certified_holding_count", 0),
                contract_dict.get("latest_agent_run_at"),
                contract_dict.get("latest_recommendation_at"),
                ",".join(contract_dict.get("agent_run_ids_used") or []) or "none",
            )
        else:
            snapshot_source = "certification_failed"
            failed_tickers_list = contract_dict.get("failed_tickers") or []
            logger.warning(
                "intel_v3_worker_certified_snapshot_rejected user_id=%s "
                "prewarm_run_id=%s snapshot_id=%s "
                "total_holding_count=%d certified_holding_count=%d "
                "failed_holding_count=%d failed_tickers=%s "
                "certification_errors=%s",
                self.user_id,
                prewarm_run_id,
                snapshot_payload.get("snapshot_id"),
                contract_dict.get("total_holding_count", 0),
                contract_dict.get("certified_holding_count", 0),
                contract_dict.get("failed_holding_count", 0),
                ",".join(failed_tickers_list[:10]) if failed_tickers_list else "none",
                ",".join(contract_dict.get("certification_errors") or []) or "none",
            )

        # Embed provenance fields into snapshot payload before persisting.
        snapshot_payload["snapshot_source"] = snapshot_source
        snapshot_payload["agents_ran_via_worker"] = True
        snapshot_payload["this_click_used_llm"] = False
        snapshot_payload["agents_ran_for_this_click"] = "No — background worker handles analysis"
        snapshot_payload["certified_holding_count"] = contract_dict.get("certified_holding_count", 0)
        snapshot_payload["total_holding_count"] = contract_dict.get("total_holding_count", 0)
        snapshot_payload["failed_tickers_in_certification"] = contract_dict.get("failed_tickers") or []
        snapshot_payload["certification_summary"] = {
            "certified": contract_certified,
            "certified_holding_count": contract_dict.get("certified_holding_count", 0),
            "total_holding_count": contract_dict.get("total_holding_count", 0),
            "failed_holding_count": contract_dict.get("failed_holding_count", 0),
            "latest_agent_run_at": contract_dict.get("latest_agent_run_at"),
            "latest_recommendation_at": contract_dict.get("latest_recommendation_at"),
            "agent_run_ids_used": contract_dict.get("agent_run_ids_used") or [],
            "certification_errors": contract_dict.get("certification_errors") or [],
        }

        # Step 6: persist.
        # When skip_persist_on_fail=True (Watchtower-triggered republish), a failed
        # certification must NOT overwrite the previous worker_certified snapshot.
        if skip_persist_on_fail and not contract_certified:
            logger.info(
                "intel_v3_prewarm_skip_persist_on_fail user_id=%s run_id=%s "
                "snapshot_source=%s — preserving previous worker_certified snapshot",
                self.user_id, prewarm_run_id,
                snapshot_payload.get("snapshot_source"),
            )
            return snapshot_payload
        await self._persist_snapshot(run_id=prewarm_run_id, payload=snapshot_payload)

        duration_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
        action_counts = snapshot_payload.get("action_counts", {})
        logger.info(
            "intel_v3_snapshot_created user_id=%s run_id=%s "
            "snapshot_id=%s total_cards=%d action_counts=%s duration_ms=%d "
            "llm_calls=0 hard_violations=0 soft_violations=%d source=%s "
            "contract_certified=%s certified_holding_count=%d total_holding_count=%d",
            self.user_id,
            prewarm_run_id,
            snapshot_payload.get("snapshot_id"),
            len(held_cards),
            action_counts,
            duration_ms,
            soft_violation_count,
            snapshot_source,
            contract_certified,
            contract_dict.get("certified_holding_count", 0),
            contract_dict.get("total_holding_count", 0),
        )
        logger.info(
            "intel_v3_ui_status_summary user_id=%s prewarm_run_id=%s "
            "snapshot_source=%s certified=%s "
            "total_holding_count=%d certified_holding_count=%d "
            "failed_holding_count=%d "
            "worker_jobs_enqueued_count=unknown worker_jobs_claimed_count=unknown "
            "attempted_llm_calls=0 persisted_ticker_success_count=unknown "
            "latest_agent_run_at=%s latest_recommendation_at=%s "
            "user_visible_status=%s",
            self.user_id,
            prewarm_run_id,
            snapshot_source,
            contract_certified,
            contract_dict.get("total_holding_count", 0),
            contract_dict.get("certified_holding_count", 0),
            contract_dict.get("failed_holding_count", 0),
            contract_dict.get("latest_agent_run_at"),
            contract_dict.get("latest_recommendation_at"),
            "Certified Current" if contract_certified else "Intel Blocked — Certification Failed",
        )

        # Evidence-depth aggregate summary — same log key as run_v3() path.
        _log_evidence_depth_summary(
            user_id=self.user_id,
            snapshot_id=snapshot_payload.get("snapshot_id", prewarm_run_id),
            run_id=prewarm_run_id,
            snapshot_payload=snapshot_payload,
            decisions=decisions,
            evidence_stats=evidence_stats,
        )

        return snapshot_payload

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _run_refresh_orchestrator(
        self,
        *,
        run_id: str,
        evidence_stats: dict[str, Any],
        evidence_adapter: Optional[ReadOnlyEvidenceAdapter] = None,
    ):
        """Build OrchestratorInputs from existing state and run the orchestrator.

        Returns None on hard failure (orchestrator never raises into the run).
        Failures degrade gracefully — run_mode stays the last classified value
        and the snapshot still persists with diagnostics reflecting truth.

        ``evidence_adapter`` is accepted for backward compatibility with the
        run_v3() call site; the orchestrator itself does not need it. Stage 3.1
        wires the non-LLM analyst refresh-request seam here, plus the Tier-0
        price refresh callable — no analyst/LLM research runs in-request.
        """
        try:
            now = datetime.now(timezone.utc)
            snap_meta = await self._get_latest_portfolio_snapshot_meta()
            tickers = await self._get_active_tickers()
            per_ticker_evidence = await self._get_per_ticker_analyst_evidence(
                tickers, now=now,
            )

            inputs = OrchestratorInputs(
                evidence_stats=evidence_stats,
                portfolio_snapshot_at=snap_meta.get("snapshot_at"),
                market_value_certified_ats=snap_meta.get("market_value_certified_ats", []),
                tickers=tickers,
                research_artifact_timestamps=[],  # v1: not yet read; explicit empty
                now=now,
                per_ticker_evidence=per_ticker_evidence,
            )

            # Build a price-refresh callable bound to the existing price engine.
            price_refresh = self._build_price_refresh_callable()
            analyst_refresh = self._build_analyst_refresh_callable()

            orchestrator = EvidenceRefreshOrchestrator(
                user_id=self.user_id,
                inputs=inputs,
                price_refresh=price_refresh,
                analyst_refresh=analyst_refresh,
                budget=RefreshBudget(),
            )
            return await orchestrator.run()
        except Exception as exc:
            logger.warning(
                "intel_v3.refresh_orchestrator_failed user_id=%s run_id=%s error=%s",
                self.user_id, run_id, exc,
            )
            return None

    async def _get_per_ticker_analyst_evidence(
        self,
        tickers: list[str],
        *,
        now: datetime,
    ) -> list[dict[str, Any]]:
        """Fetch per-ticker prior action + portfolio weight + evidence age.

        Reads ``recommendations`` (latest active per ticker) for prior action +
        created_at, and the latest portfolio snapshot for market-value weights.
        Returns a list of dicts the adapter ranks. Failures degrade to an
        empty list — the orchestrator falls back to source-level ticker scope.
        """
        if not tickers:
            return []

        rec_rows: list[dict[str, Any]] = []
        insight_rows: list[dict[str, Any]] = []

        def _fetch_recs():
            return (
                self.client.table("recommendations")
                .select("ticker,action,created_at,is_active")
                .eq("user_id", str(self.user_id))
                .in_("ticker", list(tickers))
                .order("created_at", desc=True)
                .execute()
            )

        def _fetch_insights():
            return (
                self.client.table("agent_insights")
                .select("ticker,created_at")
                .eq("user_id", str(self.user_id))
                .in_("ticker", list(tickers))
                .order("created_at", desc=True)
                .execute()
            )

        try:
            res = await asyncio.to_thread(_fetch_recs)
            rec_rows = res.data or []
        except Exception as exc:
            logger.debug(
                "intel_v3.per_ticker_recs_fetch_failed user_id=%s err=%s",
                self.user_id, exc,
            )
        try:
            res = await asyncio.to_thread(_fetch_insights)
            insight_rows = res.data or []
        except Exception as exc:
            logger.debug(
                "intel_v3.per_ticker_insights_fetch_failed user_id=%s err=%s",
                self.user_id, exc,
            )

        latest_rec_by_ticker: dict[str, dict[str, Any]] = {}
        for row in rec_rows:
            t = (row.get("ticker") or "").upper()
            if not t or t in latest_rec_by_ticker:
                continue
            latest_rec_by_ticker[t] = row

        latest_insight_by_ticker: dict[str, dict[str, Any]] = {}
        for row in insight_rows:
            t = (row.get("ticker") or "").upper()
            if not t or t in latest_insight_by_ticker:
                continue
            latest_insight_by_ticker[t] = row

        # Build weights from the latest portfolio snapshot. Best-effort.
        weight_pcts: dict[str, float] = {}
        try:
            snap_res = await asyncio.to_thread(
                lambda: self.client.table("portfolio_snapshots")
                .select("positions_data")
                .eq("user_id", str(self.user_id))
                .order("snapshot_at", desc=True)
                .limit(1)
                .execute()
            )
            rows = snap_res.data or []
            if rows:
                positions = (rows[0] or {}).get("positions_data") or []
                values: dict[str, float] = {}
                total = 0.0
                for pos in positions:
                    if not isinstance(pos, dict):
                        continue
                    t = (pos.get("ticker") or "").upper()
                    mv = pos.get("market_value") or pos.get("market_value_usd") or 0.0
                    try:
                        mv = float(mv)
                    except (TypeError, ValueError):
                        mv = 0.0
                    if t and mv > 0:
                        values[t] = mv
                        total += mv
                if total > 0:
                    for t, v in values.items():
                        weight_pcts[t] = round((v / total) * 100.0, 2)
        except Exception as exc:
            logger.debug(
                "intel_v3.per_ticker_weights_fetch_failed user_id=%s err=%s",
                self.user_id, exc,
            )

        def _age_hours(iso: Any) -> float | None:
            if not iso or not isinstance(iso, str):
                return None
            try:
                dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                return None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return round((now - dt).total_seconds() / 3600.0, 2)

        out: list[dict[str, Any]] = []
        for ticker in tickers:
            up = (ticker or "").upper()
            if not up:
                continue
            rec = latest_rec_by_ticker.get(up)
            insight = latest_insight_by_ticker.get(up)
            prior_action = (rec or {}).get("action")
            rec_age = _age_hours((rec or {}).get("created_at"))
            insight_age = _age_hours((insight or {}).get("created_at"))
            # Worst-of age: the older of the two source timestamps drives the
            # ticker's analyst-evidence staleness for ranking + classification.
            if rec_age is None and insight_age is None:
                age = None
            elif rec_age is None:
                age = insight_age
            elif insight_age is None:
                age = rec_age
            else:
                age = max(rec_age, insight_age)
            out.append({
                "ticker":              up,
                "prior_action":        prior_action,
                "weight_pct":          weight_pcts.get(up),
                "evidence_age_hours":  age,
            })
        return out

    def _build_analyst_refresh_callable(self):
        """Return the analyst refresh-request seam for the orchestrator (or None).

        Stage 3.1: the synchronous Run Intel v3 HTTP request must NOT perform
        any analyst / LLM / full-portfolio research inside the click. It wires
        the non-LLM ``AnalystRefreshRequestSeam``, which records that stale
        analyst evidence needs a refresh and returns honest deferred-ticker
        accounting so the run mode degrades to PARTIAL_CERTIFIED /
        BLOCKED_UNCERTIFIED rather than a fake FAST_CERTIFIED.

        Stage 3.2: the seam is given the Supabase client so it idempotently
        enqueues a durable ``analyst_refresh_jobs`` row per stale ticker. That
        is a fast queue upsert, not LLM/analyst work — the background
        ``analyst_refresh_worker_v1`` consumes the queue outside this request.

        The LLM adapters (``AnalystRefreshAdapter`` /
        ``FullPortfolioAnalystRefreshAdapter``) are driven by that background
        worker, never wired into this synchronous path.

        Disabled entirely by setting ``INTEL_V3_ANALYST_REFRESH_ENABLED=0``.
        """
        if not is_analyst_refresh_enabled():
            return None
        seam = AnalystRefreshRequestSeam(user_id=self.user_id, client=self.client)
        logger.info(
            "intel_v3.analyst_refresh_seam_wired user_id=%s seam=%s "
            "in_request_llm_refresh=false",
            self.user_id, type(seam).__name__,
        )
        return seam

    async def _enqueue_rationale_repair(
        self,
        *,
        run_id: str,
        tickers: list[str],
        started_at: datetime,
    ) -> None:
        """Enqueue analyst refresh jobs for tickers with ticker-prefix-only rationale.

        Called when ``certify_snapshot_cards()`` detects ``ticker_prefix_only_reason_count > 0``
        on evidence that is fresh-by-timestamp but uncertifiable (lossy analyst_verdict
        fields from pre-PR-320 writeback). Stale-age detection never fires for these
        tickers, so this is the only path that ensures the worker rewrites them.

        Uses ``enqueue_refresh_jobs()`` directly — no seam, no orchestrator, no LLM.
        The enqueue is idempotent: existing pending/failed jobs are touched/made-due;
        succeeded jobs are reopened. Never raises — DB failure degrades to a warning.
        """
        from .analyst_refresh_job_store_v1 import enqueue_refresh_jobs as _enqueue
        try:
            result = await asyncio.to_thread(
                _enqueue,
                self.client,
                user_id=self.user_id,
                tickers=tickers,
                now=started_at,
            )
            logger.info(
                "intel_v3_rationale_repair_enqueued user_id=%s run_id=%s "
                "reason=rationale_repair_required affected_tickers=%s "
                "jobs_created=%d jobs_touched=%d jobs_made_due=%d jobs_reopened=%d",
                self.user_id, run_id, tickers,
                result.created_count, result.touched_count,
                result.made_due_count, result.reopened_count,
            )
        except Exception as exc:
            logger.warning(
                "intel_v3_rationale_repair_enqueue_failed user_id=%s run_id=%s "
                "tickers=%s err=%s",
                self.user_id, run_id, tickers, exc,
            )

    async def _get_latest_portfolio_snapshot_meta(self) -> dict[str, Any]:
        """Fetch the latest portfolio_snapshots row's snapshot_at + per-position certified_at list."""
        try:
            result = await asyncio.to_thread(
                lambda: self.client.table("portfolio_snapshots")
                .select("snapshot_at,positions_data")
                .eq("user_id", str(self.user_id))
                .order("snapshot_at", desc=True)
                .limit(1)
                .execute()
            )
            rows = result.data or []
            if not rows:
                return {"snapshot_at": None, "market_value_certified_ats": []}
            row = rows[0] or {}
            certified: list[str] = []
            for pos in (row.get("positions_data") or []):
                if isinstance(pos, dict):
                    cert = pos.get("market_value_certified_at")
                    if cert:
                        certified.append(str(cert))
            return {
                "snapshot_at": row.get("snapshot_at"),
                "market_value_certified_ats": certified,
            }
        except Exception as exc:
            logger.debug(
                "intel_v3.portfolio_snapshot_meta_failed user_id=%s error=%s",
                self.user_id, exc,
            )
            return {"snapshot_at": None, "market_value_certified_ats": []}

    async def _get_active_tickers(self) -> list[str]:
        try:
            result = await asyncio.to_thread(
                lambda: self.client.table("positions")
                .select("ticker")
                .eq("user_id", str(self.user_id))
                .execute()
            )
            tickers: list[str] = []
            for row in (result.data or []):
                t = row.get("ticker") if isinstance(row, dict) else None
                if t and t not in tickers:
                    tickers.append(str(t))
            return tickers
        except Exception:
            return []

    async def _get_active_holding_context_by_ticker(self) -> dict[str, dict]:
        """Return a compact {ticker: {"category": ...}} map for explicit-run
        evidence lane eligibility (Stage 5H.3 patch).

        Best-effort: failures return {} so callers fall back to ticker-only
        dispatch. Read-only on the positions table. Never raises.
        """
        try:
            result = await asyncio.to_thread(
                lambda: self.client.table("positions")
                .select("ticker,category")
                .eq("user_id", str(self.user_id))
                .execute()
            )
            ctx: dict[str, dict] = {}
            for row in (result.data or []):
                if not isinstance(row, dict):
                    continue
                t = row.get("ticker")
                if not t:
                    continue
                category = row.get("category")
                if isinstance(category, str) and category.strip():
                    ctx.setdefault(str(t), {"category": category.strip()})
            return ctx
        except Exception:
            return {}

    def _build_price_refresh_callable(self):
        """Return an async callable (tickers -> dict) that drives PriceService.fetch_prices.

        PriceService supports keyless yfinance (stocks/ETFs) and CoinGecko
        (crypto) — paid-tier keys (Alpaca / Finnhub / Polygon) are *optional*
        accelerators, not requirements. We always return a callable when the
        module imports cleanly; the orchestrator's budget caps and per-ticker
        failure accounting handle any provider degradation honestly. The
        provider registry diagnostics separately surface which paid providers
        are env-disabled.

        Coalescing/dedupe: ``PriceService.fetch_prices`` already routes every
        ticker through ``_fetch_one`` → a per-ticker async lock, so concurrent
        callers for the same ticker collapse to one upstream call. The Tier-0
        price refresh here additionally dedupes the input ticker list before
        the call so a duplicate ticker never spawns a redundant fetch task.

        Returns None only when the module fails to import (e.g. missing
        dependency in a stripped environment); the orchestrator then records
        the refresh path as unavailable rather than calling into nothing.
        """
        try:
            # NOTE: intel_v3_service lives at `app.services.intelligence.v3`;
            # price_engine lives at `app.services.price_engine` — three dots.
            from ...price_engine import PriceService as _PriceEngine
            settings = get_settings()
        except Exception:
            return None

        async def _refresh(tickers: list[str]) -> dict[str, Any]:
            # Dedupe before the call — duplicate tickers route through the same
            # per-ticker coalescing lock anyway, but dropping them here avoids
            # spawning redundant fetch tasks under the orchestrator's budget.
            deduped: list[str] = []
            seen: set[str] = set()
            for t in tickers or []:
                key = str(t or "").upper()
                if key and key not in seen:
                    seen.add(key)
                    deduped.append(t)
            svc = _PriceEngine(
                finnhub_key=getattr(settings, "finnhub_api_key", "") or "",
                alpaca_key=getattr(settings, "alpaca_api_key", "") or "",
                alpaca_secret=getattr(settings, "alpaca_secret_key", "") or "",
                polygon_key=getattr(settings, "polygon_api_key", "") or "",
            )
            try:
                return await svc.fetch_prices(deduped)
            finally:
                try:
                    await svc.close()
                except Exception:
                    pass

        return _refresh

    async def _get_weight_map(self) -> dict[str, float]:
        """Fetch current positions and build ticker→weight_pct map."""
        try:
            # Preferred source: latest persisted portfolio snapshot market values.
            # This avoids querying non-existent columns on positions.
            snap_result = await asyncio.to_thread(
                lambda: self.client.table("portfolio_snapshots")
                .select("positions_data,snapshot_at")
                .eq("user_id", str(self.user_id))
                .order("snapshot_at", desc=True)
                .limit(1)
                .execute()
            )
            positions: list[dict[str, Any]] = []
            if snap_result.data:
                latest = snap_result.data[0] or {}
                raw_positions = latest.get("positions_data") or []
                if isinstance(raw_positions, list):
                    for row in raw_positions:
                        if not isinstance(row, dict):
                            continue
                        ticker = row.get("ticker")
                        market_value = row.get("market_value")
                        if ticker and market_value is not None:
                            positions.append({"ticker": ticker, "market_value": market_value})

            # Fallback source: positions holdings, derive value from shares*avg_cost.
            if not positions:
                result = await asyncio.to_thread(
                    lambda: self.client.table("positions")
                    .select("ticker,shares,avg_cost")
                    .eq("user_id", str(self.user_id))
                    .execute()
                )
                for row in (result.data or []):
                    ticker = row.get("ticker")
                    shares = float(row.get("shares") or 0.0)
                    avg_cost = float(row.get("avg_cost") or 0.0)
                    if ticker:
                        positions.append({"ticker": ticker, "market_value": shares * avg_cost})
            return build_weight_map(positions)
        except Exception as exc:
            logger.warning(
                "intel_v3.weight_map_failed user_id=%s error=%s", self.user_id, exc
            )
            return {}

    async def _get_evidence_shadow_for_governance(
        self,
        cards: list,
    ) -> "Optional[Any]":
        """Compute Stage 5K evidence shadow for Stage 6 governance and evidence explanation.

        Always computes the shadow (Stage 5J/5K read — cheap indexed DB query). This
        allows evidence_explanation to reflect technical/sentiment readiness regardless
        of the Stage 6 flag state. Stage 6 governance mutations (inp.evidence_quality
        changes) are applied only when intel_v3_evidence_aware_policy_enabled=True in
        the caller; this method does not enforce that gate.

        Returns ResearchEvidenceDecisionInputShadow when coverage can be computed.
        Returns None only when computation fails or there are no tickers. Never raises.

        Called once per run — not per ticker. No provider/LLM calls. No DB writes.
        Stage 5J coverage read is sync; wrapped in asyncio.to_thread.
        """
        try:
            from .research_evidence_coverage_read_model_v1 import (
                compute_research_evidence_coverage,
            )
            from .research_evidence_decision_input_adapter_v1 import (
                compute_decision_input_readiness,
            )

            tickers = [
                c.ticker.upper()
                for c in cards
                if hasattr(c, "ticker") and c.ticker
            ]
            if not tickers:
                return None

            holding_context_by_ticker = {
                c.ticker.upper(): {"category": (getattr(c, "category", "") or "")}
                for c in cards
                if hasattr(c, "ticker") and c.ticker
            }

            coverage = await asyncio.to_thread(
                lambda: compute_research_evidence_coverage(
                    user_id=str(self.user_id),
                    tickers=tickers,
                    db_client=self.client,
                )
            )

            shadow = compute_decision_input_readiness(
                coverage,
                holding_context_by_ticker=holding_context_by_ticker,
            )

            logger.info(
                "stage6_evidence_shadow_computed user_id=%s ticker_count=%d "
                "tickers_with_any_usable_axis=%d tickers_fully_missing=%d",
                self.user_id,
                shadow.portfolio_ticker_count,
                shadow.tickers_with_any_usable_axis,
                shadow.tickers_fully_missing,
            )
            return shadow

        except Exception as exc:
            logger.warning(
                "stage6_evidence_shadow_failed user_id=%s error=%s — "
                "proceeding without Stage 6 evidence governance",
                self.user_id, exc,
            )
            return None

    async def _get_sec_readiness_for_adapters(self) -> "Optional[Any]":
        """Fetch Phase 9 SEC metric readiness for Phase 11 and/or Phase 13.

        Returns SecMetricEvidenceReadinessResult when at least one of the
        Phase 11 or Phase 13 kill switches is on. Returns None when both are
        disabled or when readiness computation fails. Never raises.

        Called once per run_v3() invocation — not per ticker.
        No SQL writes. No provider calls. No LLM calls.
        """
        settings = get_settings()
        phase11_on = getattr(settings, "intel_v3_sec_metric_truth_adapter_v1_enabled", False)
        phase13_on = getattr(settings, "intel_v3_valuation_context_adapter_v1_enabled", False)
        if not (phase11_on or phase13_on):
            return None

        try:
            from ..research_workers.sec_metric_evidence_readiness_adapter import (
                compute_sec_readiness_for_phase11_adapter,
            )
            result = await asyncio.to_thread(
                compute_sec_readiness_for_phase11_adapter,
                str(self.user_id),
                self.client,
            )
            return result
        except Exception as exc:
            logger.warning(
                "intel_v3_sec_readiness_for_adapters_failed user_id=%s error=%s",
                self.user_id, exc,
            )
            return None

    async def _build_valuation_context_map(
        self,
        cards: list,
    ) -> "Optional[dict]":
        """Build a ticker→serialized-valuation-context map when the flag is enabled.

        Returns a dict when intel_v3_priceband_visible_context_v1_enabled is True.
        Returns None when disabled. Never raises — errors degrade to None silently.

        The returned map is passed to build_snapshot() so snapshot_builder can embed
        plain-English valuation context in detail_drawer_payload.valuation_context.
        Values are pre-serialized dicts (not Phase 14F objects) so snapshot_builder
        has no coupling to the priceband modules.
        """
        settings = get_settings()
        if not getattr(settings, "intel_v3_priceband_visible_context_v1_enabled", False):
            logger.info(
                "valuation_context_pr2b_summary user_id=%s flag_enabled=false "
                "bridge_not_called=true renderable_context_count=0 "
                "set_env=INTEL_V3_PRICEBAND_VISIBLE_CONTEXT_V1_ENABLED=true to enable",
                self.user_id,
            )
            return None

        try:
            from .priceband_snapshot_context_v1 import build_ticker_valuation_context_map
            tickers = [c.ticker.upper() for c in cards if hasattr(c, "ticker")]
            categories = {
                c.ticker.upper(): (c.category or "stock")
                for c in cards if hasattr(c, "ticker")
            }
            return await build_ticker_valuation_context_map(
                user_id=self.user_id,
                client=self.client,
                tickers=tickers,
                categories=categories,
            )
        except Exception as exc:
            logger.warning(
                "intel_v3_valuation_context_map_failed user_id=%s error=%s — "
                "snapshot proceeds without valuation context",
                self.user_id, exc,
            )
            return None

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


def _log_evidence_depth_summary(
    *,
    user_id: Any,
    snapshot_id: str,
    run_id: str,
    snapshot_payload: dict,
    decisions: list,
    evidence_stats: Optional[dict] = None,
) -> None:
    """Emit intel_v3_evidence_depth_summary. Shared between run_v3 and run_prewarm_snapshot.

    Production-safe: no raw metrics, no source URLs, no per-ticker payloads.
    Log key: intel_v3_evidence_depth_summary

    PR 3B extensions: adds mapped_existing_analyst_signal_count,
    trusted_signal_count_distribution, artifact_decision_safe_count,
    artifact_suppressed_unsafe_count to prove the fix without per-ticker payloads.
    """
    from collections import Counter as _Counter
    _ebc = snapshot_payload.get("evidence_band_counts", {})
    _strong_count = _ebc.get("STRONG", 0)
    _ok_count = _ebc.get("PARTIAL", 0)
    _thin_count = _ebc.get("THIN", 0)
    _sp_validated = snapshot_payload.get("source_pack_validated_count", 0)
    _sp_pending = snapshot_payload.get("source_pack_pending_count", 0)
    total_cards = len(snapshot_payload.get("current_holdings", []))
    _primary_driver_present = sum(
        1 for d in decisions
        if d.source_signal_summary.get("has_primary_driver")
    )
    _action_reason_present = sum(
        1 for d in decisions
        if d.source_signal_summary.get("has_action_reason")
    )
    _supp_counter = _Counter()
    for d in decisions:
        for k in d.suppression_reasons:
            _supp_counter[k] += 1
    _top_supp = _supp_counter.most_common(3)
    _top_supp_str = " ".join(f"{k}={v}" for k, v in _top_supp) or "none"

    # PR 3B: evidence-mapping provenance fields from the adapter stats.
    _stats = evidence_stats or {}
    _mapped_analyst = _stats.get("mapped_existing_analyst_signal_count", 0)
    _artifact_safe = _stats.get("artifact_decision_safe_count", 0)
    _artifact_suppressed = _stats.get("artifact_suppressed_unsafe_count", 0)
    _dist = _stats.get("trusted_signal_count_distribution") or {}
    _dist_str = (
        f"0={_dist.get(0,0)} 1={_dist.get(1,0)} 2={_dist.get(2,0)} 3={_dist.get(3,0)}"
        if _dist else "unavailable"
    )

    logger.info(
        "intel_v3_evidence_depth_summary user_id=%s snapshot_id=%s run_id=%s "
        "total_tickers=%d strong_count=%d ok_count=%d thin_count=%d "
        "source_pack_validated_count=%d source_pack_pending_count=%d "
        "primary_driver_present_count=%d analyst_rationale_present_count=%d "
        "mapped_existing_analyst_signal_count=%d "
        "trusted_signal_count_distribution=%s "
        "artifact_decision_safe_count=%d artifact_suppressed_unsafe_count=%d "
        "top_suppression_reasons=%s",
        user_id,
        snapshot_id,
        run_id,
        total_cards,
        _strong_count,
        _ok_count,
        _thin_count,
        _sp_validated,
        _sp_pending,
        _primary_driver_present,
        _action_reason_present,
        _mapped_analyst,
        _dist_str,
        _artifact_safe,
        _artifact_suppressed,
        _top_supp_str,
    )


def _normalize_legacy_committee_status(
    response_payload: dict,
    *,
    user_id: Any,
    snapshot_id: str,
) -> None:
    """Normalize legacy committee.status='deferred' in the API response payload.

    Response-time fix for persisted snapshots built before PR #344 introduced
    real source-pack status. Does not mutate the DB row.

    - STRONG or PARTIAL evidence_band → {status: "source_validated"}
    - THIN or unavailable             → {status: "pending", reason: <safe text>}
    - Cards with status != "deferred" pass through unchanged.

    Does not change action, conviction, evidence_band, valuation_context,
    snapshot_source, or any certification fields.
    Logs intel_v3_source_pack_legacy_normalization_summary (no raw ticker list).
    """
    holdings = response_payload.get("current_holdings")
    if not holdings:
        return

    deferred_count = sum(
        1 for c in holdings
        if (c.get("detail_drawer_payload") or {}).get("committee", {}).get("status") == "deferred"
    )
    if deferred_count == 0:
        return

    _PENDING_REASON = "Evidence not yet source-linked for this ticker."
    _STRONG_PARTIAL = {"STRONG", "PARTIAL"}

    validated_count = 0
    pending_count = 0
    unchanged_count = 0
    existing_validated = 0   # already source_validated, passed through
    existing_pending = 0     # already pending, passed through
    normalized_by_ticker: dict[str, dict] = {}
    new_holdings: list[dict] = []

    for card in holdings:
        ddp = card.get("detail_drawer_payload") or {}
        committee = ddp.get("committee") or {}
        status = committee.get("status", "")

        if status != "deferred":
            new_holdings.append(card)
            normalized_by_ticker[card.get("ticker", "")] = card
            unchanged_count += 1
            if status == "source_validated":
                existing_validated += 1
            elif status == "pending":
                existing_pending += 1
            continue

        evidence_band = card.get("evidence_band") or ddp.get("evidence_band") or "THIN"
        new_card = dict(card)
        new_ddp = dict(ddp)

        if evidence_band in _STRONG_PARTIAL:
            new_ddp["committee"] = {"status": "source_validated"}
            validated_count += 1
        else:
            existing_reason = committee.get("reason") or ""
            new_ddp["committee"] = {
                "status": "pending",
                "reason": existing_reason if existing_reason else _PENDING_REASON,
            }
            pending_count += 1

        new_card["detail_drawer_payload"] = new_ddp
        new_holdings.append(new_card)
        normalized_by_ticker[card.get("ticker", "")] = new_card

    response_payload["current_holdings"] = new_holdings

    # Keep best_buys and trim_sell_desk in sync (same card objects, same order).
    def _replace_cards(card_list: list) -> list:
        return [normalized_by_ticker.get(c.get("ticker", ""), c) for c in (card_list or [])]

    response_payload["best_buys"] = _replace_cards(response_payload.get("best_buys", []))
    response_payload["trim_sell_desk"] = _replace_cards(response_payload.get("trim_sell_desk", []))

    # Update aggregate counts to match the post-normalization committee state.
    # This keeps source_pack_validated_count / source_pack_pending_count consistent
    # with what the UI actually sees, even for old persisted snapshots.
    response_payload["source_pack_validated_count"] = validated_count + existing_validated
    response_payload["source_pack_pending_count"] = pending_count + existing_pending

    logger.info(
        "intel_v3_source_pack_legacy_normalization_summary "
        "user_id=%s snapshot_id=%s total_cards=%d "
        "normalized_to_source_validated_count=%d "
        "normalized_to_pending_count=%d unchanged_count=%d "
        "response_source_pack_validated_count=%d response_source_pack_pending_count=%d",
        user_id,
        snapshot_id,
        len(holdings),
        validated_count,
        pending_count,
        unchanged_count,
        validated_count + existing_validated,
        pending_count + existing_pending,
    )


def _stale_analyst_tickers_from_gate(gate_result: Any) -> list[str]:
    """Extract tickers with stale/missing analyst LLM evidence from gate result.

    Used by enqueue_run_v3 to only enqueue analyst refresh jobs for tickers
    where EVIDENCE_TYPE_ANALYST_LLM is not fresh/aging. Tickers with fresh
    analyst evidence are not re-enqueued, preventing a blind 34-ticker refresh
    on every Run Intel click.
    """
    from .watchtower_freshness_ledger_v1 import (
        EVIDENCE_TYPE_ANALYST_LLM,
        FRESHNESS_FRESH,
        FRESHNESS_AGING,
    )
    stale: list[str] = []
    for rec in getattr(gate_result, "evidence_records", []):
        if rec.evidence_type == EVIDENCE_TYPE_ANALYST_LLM and rec.ticker:
            if rec.freshness_status not in (FRESHNESS_FRESH, FRESHNESS_AGING):
                stale.append(rec.ticker)
    return stale


def _hash_payload(payload: dict) -> str:
    """Stable hash of snapshot payload for deduplication."""
    import hashlib, json
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ── Stage 3.2c: deterministic snapshot prewarm (module-level entry point) ────

async def prewarm_intel_v3_snapshot(
    user_id: UUID,
    *,
    prewarm_run_id: str,
) -> dict[str, Any]:
    """Build and persist a deterministic Intel v3 snapshot from persisted evidence.

    Called by the analyst refresh worker after successful evidence writeback
    (``analyst_evidence_writer_v1``) so the user does not need a second Run
    Intel click to consume the freshly written analyst rows.

    Hard guarantees:
      * Zero LLM calls — reads from ``agent_insights`` / ``recommendations``
        only; runs the deterministic ``decide()`` kernel.
      * Does NOT call ``_run_refresh_orchestrator`` — no
        ``AnalystRefreshRequestSeam`` is invoked, no ``analyst_refresh_jobs``
        rows are inserted, no recursive worker trigger is possible.
      * Raises on hard certification violations (never persists a corrupt
        snapshot); soft violations (ticker-prefix-only etc.) are logged but do
        not block persistence when the rationale fields are genuinely present.
      * Caller (``_trigger_snapshot_prewarm``) catches and logs any exception so
        worker job accounting is unaffected by prewarm failures.
    """
    svc = IntelV3Service(user_id=user_id)
    return await svc.run_prewarm_snapshot(prewarm_run_id=prewarm_run_id)
