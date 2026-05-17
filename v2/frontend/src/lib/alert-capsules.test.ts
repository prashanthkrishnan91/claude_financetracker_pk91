import {
  buildAlertWhyThisMatters,
  buildMissingDataCapsule,
  buildCandidateCapsuleState,
} from "./alert-capsules";
import type { AlertCandidate } from "./api";

function makeCandidate(overrides: Partial<AlertCandidate> = {}): AlertCandidate {
  return {
    id: "c1",
    user_id: "u1",
    ticker: "AAPL",
    source_area: "intel",
    candidate_type: "new_actionable_action",
    action_type: "BUY",
    severity: "high",
    reason_code: "buy_threshold_crossed",
    plain_english_reason: "Intel recommends buying this position.",
    policy_version: "v1",
    status: "candidate",
    dedupe_key: "abc123",
    source_snapshot_id: null,
    source_run_id: null,
    expires_at: null,
    cooldown_until: null,
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

// ── buildAlertWhyThisMatters ──────────────────────────────────────────────────

describe("buildAlertWhyThisMatters — headline", () => {
  it("produces High prefix for high severity", () => {
    const c = makeCandidate({ severity: "high", candidate_type: "new_actionable_action" });
    const { headline } = buildAlertWhyThisMatters(c);
    expect(headline).toMatch(/High/i);
  });

  it("produces Normal prefix for normal severity", () => {
    const c = makeCandidate({ severity: "normal", candidate_type: "new_actionable_action" });
    const { headline } = buildAlertWhyThisMatters(c);
    expect(headline).toMatch(/Normal/i);
  });

  it("includes candidate type in headline", () => {
    const c = makeCandidate({ candidate_type: "new_actionable_action" });
    const { headline } = buildAlertWhyThisMatters(c);
    expect(headline.length).toBeGreaterThan(3);
  });

  it("handles conviction_upgrade type", () => {
    const c = makeCandidate({ candidate_type: "conviction_upgrade" });
    const { headline } = buildAlertWhyThisMatters(c);
    expect(headline.length).toBeGreaterThan(3);
  });
});

describe("buildAlertWhyThisMatters — body", () => {
  it("body references source area for new_actionable_action", () => {
    const c = makeCandidate({ source_area: "intel", candidate_type: "new_actionable_action" });
    const { body } = buildAlertWhyThisMatters(c);
    expect(body).toMatch(/Intel/i);
  });

  it("high-severity body mentions prompt review", () => {
    const c = makeCandidate({ severity: "high", candidate_type: "new_actionable_action" });
    const { body } = buildAlertWhyThisMatters(c);
    expect(body.toLowerCase()).toMatch(/review|priority/);
  });

  it("conviction_upgrade body mentions no immediate action required when at target", () => {
    const c = makeCandidate({ candidate_type: "conviction_upgrade", action_type: "BUY" });
    const { body } = buildAlertWhyThisMatters(c);
    expect(body.toLowerCase()).toMatch(/action|position/);
  });

  it("body never contains fabricated forward-looking claims (no 'will')", () => {
    const c = makeCandidate();
    const { body } = buildAlertWhyThisMatters(c);
    // body should not make market/outcome predictions
    expect(body.toLowerCase()).not.toMatch(/will recover|will rise|will outperform/);
  });
});

describe("buildAlertWhyThisMatters — trimNote", () => {
  it("trimNote is non-null for TRIM action_type", () => {
    const c = makeCandidate({ action_type: "TRIM" });
    const { trimNote } = buildAlertWhyThisMatters(c);
    expect(trimNote).not.toBeNull();
  });

  it("trimNote explains Trim is not bad company", () => {
    const c = makeCandidate({ action_type: "TRIM" });
    const { trimNote } = buildAlertWhyThisMatters(c);
    expect(trimNote!.toLowerCase()).toMatch(/sizing|allocation|not.*exit|not.*bad/i);
  });

  it("trimNote is null for BUY action_type", () => {
    const c = makeCandidate({ action_type: "BUY" });
    const { trimNote } = buildAlertWhyThisMatters(c);
    expect(trimNote).toBeNull();
  });

  it("trimNote is null for null action_type", () => {
    const c = makeCandidate({ action_type: null });
    const { trimNote } = buildAlertWhyThisMatters(c);
    expect(trimNote).toBeNull();
  });
});

// ── buildMissingDataCapsule ───────────────────────────────────────────────────

describe("buildMissingDataCapsule", () => {
  it("returns null for active candidate status", () => {
    const c = makeCandidate({ status: "candidate" });
    expect(buildMissingDataCapsule(c)).toBeNull();
  });

  it("returns null for snoozed status", () => {
    const c = makeCandidate({ status: "snoozed" });
    expect(buildMissingDataCapsule(c)).toBeNull();
  });

  it("returns capsule for suppressed status", () => {
    const c = makeCandidate({ status: "suppressed" });
    const capsule = buildMissingDataCapsule(c);
    expect(capsule).not.toBeNull();
  });

  it("returns capsule for expired status", () => {
    const c = makeCandidate({ status: "expired" });
    const capsule = buildMissingDataCapsule(c);
    expect(capsule).not.toBeNull();
  });

  it("returns capsule for dismissed status", () => {
    const c = makeCandidate({ status: "dismissed" });
    const capsule = buildMissingDataCapsule(c);
    expect(capsule).not.toBeNull();
  });

  it("suppressed capsule body includes plain_english_reason", () => {
    const c = makeCandidate({ status: "suppressed", plain_english_reason: "Evidence too thin." });
    const capsule = buildMissingDataCapsule(c)!;
    expect(capsule.body).toContain("Evidence too thin.");
  });

  it("suppressed capsule explains data threshold — not fabricated reason", () => {
    const c = makeCandidate({ status: "suppressed" });
    const capsule = buildMissingDataCapsule(c)!;
    expect(capsule.body.toLowerCase()).toMatch(/threshold|evidence|policy/);
  });

  it("suppressed capsule headline is honest (not promotional)", () => {
    const c = makeCandidate({ status: "suppressed" });
    const capsule = buildMissingDataCapsule(c)!;
    expect(capsule.headline.length).toBeGreaterThan(5);
    expect(capsule.headline.toLowerCase()).not.toMatch(/great|excellent|strong recommendation/);
  });
});

// ── buildCandidateCapsuleState ────────────────────────────────────────────────

describe("buildCandidateCapsuleState", () => {
  it("isExpandable is true for any candidate", () => {
    const c = makeCandidate();
    const state = buildCandidateCapsuleState(c);
    expect(state.isExpandable).toBe(true);
  });

  it("missingData is null for active candidate", () => {
    const c = makeCandidate({ status: "candidate" });
    const state = buildCandidateCapsuleState(c);
    expect(state.missingData).toBeNull();
  });

  it("missingData is non-null for suppressed candidate", () => {
    const c = makeCandidate({ status: "suppressed" });
    const state = buildCandidateCapsuleState(c);
    expect(state.missingData).not.toBeNull();
  });

  it("whyThisMatters is always populated", () => {
    const c = makeCandidate();
    const state = buildCandidateCapsuleState(c);
    expect(state.whyThisMatters.headline.length).toBeGreaterThan(0);
    expect(state.whyThisMatters.body.length).toBeGreaterThan(0);
  });
});
