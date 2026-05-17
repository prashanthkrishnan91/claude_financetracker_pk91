"use client";

/**
 * TrustPrimitives — Stage 4D shared trust/evidence UI primitives.
 *
 * Small reusable components for the evidence shell and data health layer.
 * Uses existing design-system tokens (action-*, text-*, bg-*, border-*).
 * No new component library. No motion library.
 */

import { cn } from "@/lib/utils";
import type { DataHealthStatus } from "@/lib/intel-v3-evidence";

// ── DataHealthPill ────────────────────────────────────────────────────────────

const DATA_HEALTH_STYLES: Record<DataHealthStatus, string> = {
  ok:          "bg-action-buy/10 text-action-buy border-action-buy/20",
  pending:     "bg-action-hold/10 text-action-hold border-action-hold/20",
  blocked:     "bg-action-sell/10 text-action-sell border-action-sell/20",
  unavailable: "bg-surface-elevated text-text-muted border-border",
};

const DATA_HEALTH_LABELS: Record<DataHealthStatus, string> = {
  ok:          "OK",
  pending:     "Pending",
  blocked:     "Blocked",
  unavailable: "—",
};

export function DataHealthPill({
  status,
  className,
}: {
  status: DataHealthStatus;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center text-[10px] px-1.5 py-0.5 rounded border font-medium tracking-wide",
        DATA_HEALTH_STYLES[status],
        className
      )}
    >
      {DATA_HEALTH_LABELS[status]}
    </span>
  );
}

// ── TrustStatusRow ────────────────────────────────────────────────────────────

export function TrustStatusRow({
  label,
  status,
  detail,
  className,
}: {
  label: string;
  status: DataHealthStatus;
  detail: string;
  className?: string;
}) {
  return (
    <div className={cn("flex items-start justify-between gap-3 py-2.5", className)}>
      <div className="min-w-0">
        <p className="text-xs font-medium text-text-primary leading-tight">{label}</p>
        <p className="text-[10px] text-text-muted leading-snug mt-0.5">{detail}</p>
      </div>
      <DataHealthPill status={status} className="shrink-0 mt-0.5" />
    </div>
  );
}

// ── SourceMetadataStrip ───────────────────────────────────────────────────────

export function SourceMetadataStrip({
  updatedAt,
  snapshotIdShort,
  runIdShort,
  schemaVersion,
  className,
}: {
  updatedAt?: string;
  snapshotIdShort?: string;
  runIdShort?: string;
  schemaVersion?: string;
  className?: string;
}) {
  return (
    <div className={cn("text-[10px] text-text-muted space-y-0.5", className)}>
      {updatedAt && <p>Updated: {updatedAt}</p>}
      {snapshotIdShort && <p>Snapshot: {snapshotIdShort}</p>}
      {runIdShort && <p>Run: {runIdShort}</p>}
      {schemaVersion && <p>Schema: {schemaVersion}</p>}
    </div>
  );
}

// ── DataUnavailableCallout ────────────────────────────────────────────────────

export function DataUnavailableCallout({
  label = "Not connected to this view yet",
  className,
}: {
  label?: string;
  className?: string;
}) {
  return (
    <p className={cn("text-[10px] text-text-muted italic py-1", className)}>
      {label}
    </p>
  );
}

// ── EvidenceShell ─────────────────────────────────────────────────────────────

export function EvidenceShell({
  title,
  children,
  className,
}: {
  title: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "rounded-lg border border-border bg-surface/30 px-4 py-3 space-y-2",
        className
      )}
    >
      <p className="text-[10px] font-semibold uppercase tracking-widest text-text-muted">
        {title}
      </p>
      <div>{children}</div>
    </div>
  );
}
