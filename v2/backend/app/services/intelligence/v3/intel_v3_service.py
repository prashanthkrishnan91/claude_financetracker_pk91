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

from ....config import get_settings
from ....database import get_supabase_client
from .decision_policy_v1 import decide
from .existing_signal_adapter import build_truth_aware_decision_input
from .evidence_refresh_orchestrator_v1 import (
    EvidenceRefreshOrchestrator,
    OrchestratorInputs,
    RefreshBudget,
)
from .read_only_evidence_adapter import ReadOnlyEvidenceAdapter
from .portfolio_governor_lite import build_weight_map, compute_portfolio_fit
from .snapshot_builder import build_snapshot
from .snapshot_freshness_diagnostics import build_diagnostics
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

                decision = decide(inp)
                decisions.append(decision)

                card_metas.append({
                    "ticker":      ticker,
                    "name":        card.name or ticker,
                    "category":    category,
                    "thesis_state": "intact",
                })

            # Step 4: build snapshot (without diagnostics initially; diagnostics need the payload).
            snapshot_payload = build_snapshot(
                run_id=run_id,
                decisions=decisions,
                card_metas=card_metas,
                source_health={"status": "signals_from_existing_cards"},
                is_stale=False,
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

            return snapshot_payload

        except Exception as exc:
            logger.error(
                "intel_v3_run_failed user_id=%s run_id=%s error=%s",
                self.user_id, run_id, exc,
            )
            raise

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _run_refresh_orchestrator(
        self,
        *,
        run_id: str,
        evidence_stats: dict[str, Any],
    ):
        """Build OrchestratorInputs from existing state and run the orchestrator.

        Returns None on hard failure (orchestrator never raises into the run).
        Failures degrade gracefully — run_mode stays the last classified value
        and the snapshot still persists with diagnostics reflecting truth.
        """
        try:
            now = datetime.now(timezone.utc)
            snap_meta = await self._get_latest_portfolio_snapshot_meta()
            tickers = await self._get_active_tickers()

            inputs = OrchestratorInputs(
                evidence_stats=evidence_stats,
                portfolio_snapshot_at=snap_meta.get("snapshot_at"),
                market_value_certified_ats=snap_meta.get("market_value_certified_ats", []),
                tickers=tickers,
                research_artifact_timestamps=[],  # v1: not yet read; explicit empty
                now=now,
            )

            # Build a price-refresh callable bound to the existing price engine.
            price_refresh = self._build_price_refresh_callable()

            orchestrator = EvidenceRefreshOrchestrator(
                user_id=self.user_id,
                inputs=inputs,
                price_refresh=price_refresh,
                analyst_refresh=None,  # v1: analyst refresh not wired
                budget=RefreshBudget(),
            )
            return await orchestrator.run()
        except Exception as exc:
            logger.warning(
                "intel_v3.refresh_orchestrator_failed user_id=%s run_id=%s error=%s",
                self.user_id, run_id, exc,
            )
            return None

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

    def _build_price_refresh_callable(self):
        """Return an async callable (tickers -> dict) that drives PriceService.fetch_prices.

        Returns None when env keys are missing so the orchestrator can record
        the refresh path as unavailable rather than calling into broken
        providers and inflating failed_provider_calls.
        """
        try:
            from ..price_engine import PriceService as _PriceEngine
            settings = get_settings()
        except Exception:
            return None

        keys_present = any([
            getattr(settings, "alpaca_api_key", None),
            getattr(settings, "finnhub_api_key", None),
            getattr(settings, "polygon_api_key", None),
        ])
        if not keys_present:
            return None

        async def _refresh(tickers: list[str]) -> dict[str, Any]:
            svc = _PriceEngine(
                finnhub_key=settings.finnhub_api_key or "",
                alpaca_key=settings.alpaca_api_key or "",
                alpaca_secret=settings.alpaca_secret_key or "",
                polygon_key=settings.polygon_api_key or "",
            )
            try:
                return await svc.fetch_prices(tickers)
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
