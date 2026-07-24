/**
 * @jest-environment jsdom
 *
 * Render-level proof for EvidenceSummaryBand's fail-closed "unknown" copy.
 *
 * Release-blocker requirement: when run_trust_contract.overall_status is
 * "unknown" (a read/reverification failure), the evidence-lane explanation
 * paragraph must NEVER reuse the established-thin-signal copy — we do not
 * know signals are merely thin, that fundamentals were reverified, or that
 * the only impact is a confidence cap. It must instead render an honest,
 * conservative "could not be re-verified" explanation.
 */

import React from "react";
import { act } from "react-dom/test-utils";
import { createRoot, Root } from "react-dom/client";

// IntelV3HoldingsPanel pulls in DataHealthDrawer -> hooks.ts -> api.ts ->
// supabase.ts, which eagerly calls createClient() at module load and
// throws without real Supabase env vars. This render test only needs the
// pure UI output, not a live client — mock the leaf module (jest.mock is
// hoisted above the imports below by ts-jest).
jest.mock("@/lib/supabase", () => ({ supabase: {} }));

import { IntelV3HoldingsPanel } from "./IntelV3HoldingsPanel";
import type { IntelV3HeldCard, IntelV3Snapshot } from "@/lib/api";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function unknownCard(ticker: string): IntelV3HeldCard {
  return {
    ticker,
    name: `${ticker} Inc`,
    asset_type: "equity",
    action: "HOLD",
    conviction: "MEDIUM",
    evidence_band: "PARTIAL",
    portfolio_fit: "ON_TARGET",
    risk_level: "MODERATE",
    thesis_state: "active",
    why_text: "Trust status could not be re-verified for this holding — treat as unresolved.",
    risk_text: "",
    action_text: "Trust status could not be re-verified for this holding — treat as unresolved.",
    what_would_change_view: "",
    fit_text: "",
    evidence_text: "",
    flags: [],
    source_snapshot_id: "snap-unknown",
    source_run_id: "sess-unknown",
    updated_at: "2026-07-24T00:00:00Z",
    detail_drawer_payload: {
      rationale: "Trust status could not be re-verified for this holding — treat as unresolved.",
      why_now: "", why_not_now: "",
      evidence_band: "PARTIAL", evidence_quality: "OK", attractiveness: "OK",
      price_context: "SUPPRESSED", portfolio_fit_raw: "ON_TARGET", risk_band: "MODERATE",
      blockers: [], suppression_reasons: {}, schema_version: "v3.1",
      committee: { status: "pending" },
      evidence_explanation: {
        primary_evidence_status: "READY", technical_signals_status: "READY", sentiment_status: "READY",
        conviction_cap_applied: false, conviction_cap_reason: "", safe_for_visible_decision: true,
        safe_for_visible_decision_reason: "", governance_priority: "", corroboration_gap: false, action_blocks: [],
      },
      source_lineage: { status: "unknown", has_source_refs: false },
      conflict_review_status: "unknown",
      decision_constraints: [],
      trust_status: "unknown",
      decision_bands: { evidence_quality: "OK", price_context: "SUPPRESSED", portfolio_fit: "ON_TARGET", risk_band: "MODERATE", attractiveness: "OK" },
      asset_intelligence_context: null,
    },
  } as unknown as IntelV3HeldCard;
}

function makeUnknownSnapshot(): IntelV3Snapshot {
  const reason = "Session row could not be found — trust status could not be re-verified.";
  const cards = [unknownCard("AAPL"), unknownCard("MSFT")];
  return {
    schema_version: "v3.1", snapshot_id: "snap-unknown", run_id: "sess-unknown",
    generated_at: "2026-07-24T00:00:00Z", is_stale: false,
    source_health: { status: "unknown", reason },
    portfolio_command_center: {
      total_holdings: 2, buy_count: 0, hold_count: 2, trim_count: 0, sell_count: 0,
      high_conviction: 0, thin_evidence: 0, source_health: { status: "unknown", reason },
    },
    action_counts: { BUY: 0, HOLD: 2, TRIM: 0, SELL: 0 },
    evidence_band_counts: { THIN: 0, PARTIAL: 2, STRONG: 0 },
    conviction_counts: { LOW: 0, MEDIUM: 2, HIGH: 0 },
    source_pack_validated_count: 0, source_pack_pending_count: 2,
    best_buys: [], trim_sell_desk: [], current_holdings: cards,
    opportunity_radar_preview: { status: "deferred" },
    what_changed: [], warnings: [reason], legacy_path_used: false,
    session_status: "completed",
    session_coverage: {
      frozen_holding_count: 2, decided_count: 2, no_call_count: 0, failed_count: 0,
      no_call_tickers: [], failed_tickers: [], gaps: [],
    },
    run_trust_contract: {
      schema_version: "run_trust_contract_v1",
      run_session_id: "sess-unknown",
      generated_at: "2026-07-24T00:00:00Z",
      overall_status: "unknown",
      session_coverage: {
        frozen_holding_count: 0, decided_count: 0, no_call_count: 0,
        failed_count: 0, unaccounted_count: 0, publication_complete: false,
      },
      axis_coverage: {},
      conflict_review_coverage: {
        required_count: 0, succeeded_count: 0, failed_count: 0, pending_count: 0,
        required_tickers: [], succeeded_tickers: [], failed_tickers: [], pending_tickers: [],
      },
      source_lineage: {
        outputs_with_source_refs: 0, outputs_missing_source_refs: 0,
        tickers_with_lineage: [], tickers_missing_lineage: [],
        tickers_full_lineage: [], tickers_partial_lineage: [], tickers_missing_lineage_full: [],
      },
      source_health: { status: "unknown", reason },
      ticker_trust: [],
      blocking_reasons: [reason],
      warnings: [reason],
    },
    evidence_freshness_state: "certified_current",
  } as unknown as IntelV3Snapshot;
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("IntelV3HoldingsPanel — EvidenceSummaryBand fail-closed unknown copy", () => {
  it("renders truthful unknown copy, never the established thin-signal explanation", () => {
    act(() => {
      root.render(
        <IntelV3HoldingsPanel snapshot={makeUnknownSnapshot()} isLoading={false} noSnapshot={false} />,
      );
    });
    const text = container.textContent ?? "";

    // Three evidence chips say "could not be verified".
    expect(text).toContain("Company data could not be verified");
    expect(text).toContain("Price signals could not be verified");
    expect(text).toContain("News & sentiment could not be verified");

    // Distinct trust-unknown count.
    expect(text).toContain("trust unknown");

    // The new fail-closed explanation.
    expect(text).toContain("Evidence-source status could not be re-verified");
    expect(text).toContain("Support claims are withheld until the durable run data can be read again");

    // The established-thin-signal copy must be completely absent.
    expect(text).not.toContain("supporting signals are thin");
    expect(text).not.toContain("Recommendations still reflect company fundamentals");
    expect(text).not.toContain("missing signals cause confidence caps");
    expect(text.toLowerCase()).not.toContain("not applicable");
  });
});
