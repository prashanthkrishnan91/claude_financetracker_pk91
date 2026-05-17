/**
 * Pure data-mapping helpers for the Alert Center UI.
 * No IO, no side effects. Safe to test in isolation.
 */

export const CANDIDATE_STATUS_LABEL: Record<string, string> = {
  candidate: "Pending Review",
  suppressed: "Skipped",
  snoozed: "Snoozed",
  dismissed: "Dismissed",
  expired: "Expired",
};

export const OUTBOX_STATUS_LABEL: Record<string, string> = {
  pending: "Queued",
  processing: "Processing",
  suppressed: "Dry-Run Only",
  sent: "Sent",
  failed: "Failed",
  cancelled: "Cancelled",
};

export const CANDIDATE_TYPE_LABEL: Record<string, string> = {
  new_actionable_action: "New Signal",
  conviction_upgrade: "Conviction Upgrade",
};

export const SOURCE_AREA_LABEL: Record<string, string> = {
  intel: "Intel",
  deploy: "Deploy",
  watchtower: "Watchtower",
  alert: "Alert",
};

/** Translate raw severity string to a user-facing label. */
export function severityLabel(severity: string): string {
  switch (severity.toLowerCase()) {
    case "high":
      return "High";
    case "normal":
      return "Normal";
    default:
      return "Low";
  }
}

/** Translate a candidate status to its plain-English label, with fallback. */
export function candidateStatusLabel(status: string): string {
  return CANDIDATE_STATUS_LABEL[status] ?? status;
}

/** Translate an outbox status to its plain-English label, with fallback. */
export function outboxStatusLabel(status: string): string {
  return OUTBOX_STATUS_LABEL[status] ?? status;
}

/** Translate a candidate type to its plain-English label, with fallback. */
export function candidateTypeLabel(candidateType: string): string {
  return CANDIDATE_TYPE_LABEL[candidateType] ?? candidateType;
}

/** Translate a source area key to its plain-English label, with fallback. */
export function sourceAreaLabel(sourceArea: string): string {
  return SOURCE_AREA_LABEL[sourceArea] ?? sourceArea;
}

/**
 * Build a human-readable relative time string from an ISO timestamp.
 * Returns a plain string — formatting is handled by the caller.
 */
export function relativeTimeLabel(iso: string, now: number = Date.now()): string {
  const diffMs = now - new Date(iso).getTime();
  const diffMin = Math.floor(diffMs / 60_000);
  const diffHr = Math.floor(diffMin / 60);
  const diffDay = Math.floor(diffHr / 24);

  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  if (diffHr < 24) return `${diffHr}h ago`;
  return `${diffDay}d ago`;
}
