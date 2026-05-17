import {
  candidateStatusLabel,
  outboxStatusLabel,
  candidateTypeLabel,
  sourceAreaLabel,
  severityLabel,
  relativeTimeLabel,
} from "./alert-center";

describe("candidateStatusLabel", () => {
  it("maps candidate to Pending Review", () => {
    expect(candidateStatusLabel("candidate")).toBe("Pending Review");
  });
  it("maps suppressed to Skipped", () => {
    expect(candidateStatusLabel("suppressed")).toBe("Skipped");
  });
  it("maps snoozed to Snoozed", () => {
    expect(candidateStatusLabel("snoozed")).toBe("Snoozed");
  });
  it("maps dismissed to Dismissed", () => {
    expect(candidateStatusLabel("dismissed")).toBe("Dismissed");
  });
  it("maps expired to Expired", () => {
    expect(candidateStatusLabel("expired")).toBe("Expired");
  });
  it("returns raw value for unknown status", () => {
    expect(candidateStatusLabel("unknown_status")).toBe("unknown_status");
  });
});

describe("outboxStatusLabel", () => {
  it("maps pending to Queued", () => {
    expect(outboxStatusLabel("pending")).toBe("Queued");
  });
  it("maps processing to Processing", () => {
    expect(outboxStatusLabel("processing")).toBe("Processing");
  });
  it("maps suppressed to Dry-Run Only", () => {
    expect(outboxStatusLabel("suppressed")).toBe("Dry-Run Only");
  });
  it("maps sent to Sent", () => {
    expect(outboxStatusLabel("sent")).toBe("Sent");
  });
  it("maps failed to Failed", () => {
    expect(outboxStatusLabel("failed")).toBe("Failed");
  });
  it("maps cancelled to Cancelled", () => {
    expect(outboxStatusLabel("cancelled")).toBe("Cancelled");
  });
  it("returns raw value for unknown status", () => {
    expect(outboxStatusLabel("processing_v2")).toBe("processing_v2");
  });
});

describe("candidateTypeLabel", () => {
  it("maps new_actionable_action to New Signal", () => {
    expect(candidateTypeLabel("new_actionable_action")).toBe("New Signal");
  });
  it("maps conviction_upgrade to Conviction Upgrade", () => {
    expect(candidateTypeLabel("conviction_upgrade")).toBe("Conviction Upgrade");
  });
  it("returns raw value for unknown type", () => {
    expect(candidateTypeLabel("future_type")).toBe("future_type");
  });
});

describe("sourceAreaLabel", () => {
  it("maps intel to Intel", () => {
    expect(sourceAreaLabel("intel")).toBe("Intel");
  });
  it("maps watchtower to Watchtower", () => {
    expect(sourceAreaLabel("watchtower")).toBe("Watchtower");
  });
  it("maps deploy to Deploy", () => {
    expect(sourceAreaLabel("deploy")).toBe("Deploy");
  });
  it("returns raw value for unknown area", () => {
    expect(sourceAreaLabel("custom")).toBe("custom");
  });
});

describe("severityLabel", () => {
  it("maps high (any case) to High", () => {
    expect(severityLabel("high")).toBe("High");
    expect(severityLabel("HIGH")).toBe("High");
  });
  it("maps normal to Normal", () => {
    expect(severityLabel("normal")).toBe("Normal");
  });
  it("maps low to Low", () => {
    expect(severityLabel("low")).toBe("Low");
  });
  it("defaults unknown severity to Low", () => {
    expect(severityLabel("medium")).toBe("Low");
  });
});

describe("relativeTimeLabel", () => {
  const BASE = new Date("2026-05-17T12:00:00Z").getTime();

  it("returns just now for < 1 minute ago", () => {
    const iso = new Date(BASE - 30_000).toISOString();
    expect(relativeTimeLabel(iso, BASE)).toBe("just now");
  });

  it("returns minutes ago for < 1 hour", () => {
    const iso = new Date(BASE - 15 * 60_000).toISOString();
    expect(relativeTimeLabel(iso, BASE)).toBe("15m ago");
  });

  it("returns hours ago for < 24 hours", () => {
    const iso = new Date(BASE - 3 * 3600_000).toISOString();
    expect(relativeTimeLabel(iso, BASE)).toBe("3h ago");
  });

  it("returns days ago for >= 24 hours", () => {
    const iso = new Date(BASE - 2 * 24 * 3600_000).toISOString();
    expect(relativeTimeLabel(iso, BASE)).toBe("2d ago");
  });
});
