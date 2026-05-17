"use client";

import { cn } from "@/lib/utils";
import { useAlertCandidates, useAlertOutbox } from "@/lib/hooks";
import { InlineLoader } from "@/components/ui/Spinner";
import { EmptyState } from "@/components/ui/EmptyState";
import {
  candidateStatusLabel,
  outboxStatusLabel,
  candidateTypeLabel,
  sourceAreaLabel,
  severityLabel,
  relativeTimeLabel,
} from "@/lib/alert-center";
import { buildCandidateCapsuleState } from "@/lib/alert-capsules";
import {
  WhyThisMattersCapsule,
  WhyTrimIsNotBadCompanyCapsule,
  WhatMissingDataMeansCapsule,
  ExpandableCapsule,
} from "@/components/cards/DeterministicCapsules";
import type { AlertCandidate, AlertDeliveryOutbox } from "@/lib/api";

// ── Style helpers ─────────────────────────────────────────────────────────────

function severityStyle(severity: string) {
  switch (severity.toLowerCase()) {
    case "high":
      return "bg-red-500/10 text-red-400 border border-red-500/25";
    case "normal":
      return "bg-yellow-500/10 text-yellow-400 border border-yellow-500/25";
    default:
      return "bg-blue-500/10 text-blue-400 border border-blue-500/25";
  }
}

function candidateStatusStyle(status: string) {
  switch (status) {
    case "candidate":
      return "bg-accent/10 text-accent border border-accent/25";
    case "suppressed":
    case "dismissed":
    case "expired":
      return "bg-surface-elevated text-text-muted border border-border";
    case "snoozed":
      return "bg-yellow-500/10 text-yellow-400 border border-yellow-500/25";
    default:
      return "bg-surface-elevated text-text-secondary border border-border";
  }
}

function outboxStatusStyle(status: string) {
  switch (status) {
    case "sent":
      return "bg-green-500/10 text-green-400 border border-green-500/25";
    case "pending":
    case "processing":
      return "bg-accent/10 text-accent border border-accent/25";
    case "suppressed":
      return "bg-yellow-500/10 text-yellow-400 border border-yellow-500/25";
    case "failed":
      return "bg-red-500/10 text-red-400 border border-red-500/25";
    default:
      return "bg-surface-elevated text-text-muted border border-border";
  }
}

function actionStyle(action: string | null) {
  switch (action) {
    case "BUY":
      return "bg-green-500/10 text-green-400 border border-green-500/25";
    case "TRIM":
      return "bg-yellow-500/10 text-yellow-400 border border-yellow-500/25";
    case "SELL":
      return "bg-red-500/10 text-red-400 border border-red-500/25";
    default:
      return "bg-surface-elevated text-text-secondary border border-border";
  }
}

// ── Shared pill ───────────────────────────────────────────────────────────────

function Pill({ label, className }: { label: string; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-semibold tracking-wide uppercase",
        className
      )}
    >
      {label}
    </span>
  );
}

function RelativeTime({ iso }: { iso: string }) {
  return (
    <time
      className="text-[11px] text-text-muted"
      dateTime={iso}
      title={new Date(iso).toLocaleString()}
    >
      {relativeTimeLabel(iso)}
    </time>
  );
}

// ── Candidate row ─────────────────────────────────────────────────────────────

function CandidateRow({ c }: { c: AlertCandidate }) {
  const capsule = buildCandidateCapsuleState(c);
  const isSuppressed = c.status === "suppressed" || c.status === "expired" || c.status === "dismissed";
  const isTrim = c.action_type === "TRIM";

  return (
    <div className="flex flex-col gap-2 py-3.5 px-4 border-b border-border last:border-b-0">
      {/* Top row: ticker + action + severity + type + source */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-sm font-semibold text-text-primary tracking-tight">
          {c.ticker}
        </span>
        {c.action_type && (
          <Pill label={c.action_type} className={actionStyle(c.action_type)} />
        )}
        <Pill label={candidateStatusLabel(c.status)} className={candidateStatusStyle(c.status)} />
        <Pill label={severityLabel(c.severity)} className={severityStyle(c.severity)} />
        <Pill
          label={candidateTypeLabel(c.candidate_type)}
          className="bg-surface-elevated text-text-secondary border border-border"
        />
        <Pill
          label={sourceAreaLabel(c.source_area)}
          className="bg-surface-elevated text-text-muted border border-border"
        />
      </div>

      {/* Plain-English reason from backend */}
      <p className="text-xs text-text-secondary leading-relaxed">{c.plain_english_reason}</p>

      {/* Timestamps + cooldown */}
      <div className="flex items-center gap-3">
        <RelativeTime iso={c.created_at} />
        {c.cooldown_until && (
          <span className="text-[11px] text-text-muted">
            Cooldown until {new Date(c.cooldown_until).toLocaleDateString()}
          </span>
        )}
      </div>

      {/* Deterministic capsule expansion */}
      <ExpandableCapsule triggerLabel="Why this matters">
        <div className="space-y-2 mt-1">
          <WhyThisMattersCapsule
            headline={capsule.whyThisMatters.headline}
            body={capsule.whyThisMatters.body}
          />
          {isTrim && <WhyTrimIsNotBadCompanyCapsule />}
          {isSuppressed && capsule.missingData && (
            <WhatMissingDataMeansCapsule detail={capsule.missingData.body} />
          )}
        </div>
      </ExpandableCapsule>
    </div>
  );
}

// ── Outbox row ────────────────────────────────────────────────────────────────

function OutboxRow({ o }: { o: AlertDeliveryOutbox }) {
  return (
    <div className="flex flex-col gap-1.5 py-3 px-4 border-b border-border last:border-b-0">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-sm font-semibold text-text-primary tracking-tight">
          {o.ticker}
        </span>
        <Pill label={outboxStatusLabel(o.status)} className={outboxStatusStyle(o.status)} />
        <Pill
          label={o.channel === "email" ? "Email" : o.channel}
          className="bg-surface-elevated text-text-secondary border border-border"
        />
        <Pill label={severityLabel(o.severity)} className={severityStyle(o.severity)} />
      </div>
      <p className="text-xs text-text-secondary leading-relaxed line-clamp-2">{o.subject}</p>
      <div className="flex items-center gap-3 mt-0.5">
        <RelativeTime iso={o.created_at} />
        {o.sent_at && (
          <span className="text-[11px] text-text-muted">
            Sent {new Date(o.sent_at).toLocaleDateString()}
          </span>
        )}
        {o.failure_reason && (
          <span className="text-[11px] text-red-400">{o.failure_reason}</span>
        )}
      </div>
    </div>
  );
}

// ── Section card shell ────────────────────────────────────────────────────────

function SectionCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-border bg-surface overflow-hidden">
      <div className="px-4 py-3 border-b border-border bg-surface-elevated/50">
        <h2 className="text-xs font-semibold uppercase tracking-[0.08em] text-text-muted">
          {title}
        </h2>
      </div>
      {children}
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function AlertCenterPage() {
  const { data: candidates, isLoading: candLoading, error: candError } = useAlertCandidates();
  const { data: outbox, isLoading: outboxLoading, error: outboxError } = useAlertOutbox();

  return (
    <div className="max-w-2xl mx-auto px-4 py-6 space-y-5">
      {/* Header */}
      <div>
        <p className="text-[10px] font-mono uppercase tracking-[0.14em] text-accent opacity-50">
          Watchtower
        </p>
        <h1 className="text-xl font-semibold text-text-primary tracking-tight mt-0.5">
          Review Queue
        </h1>
        <p className="text-xs text-text-muted mt-1">
          Signals generated by Watchtower when Intel thresholds are crossed. Review before acting.
        </p>
      </div>

      {/* Dry-Run Safety Notice — always visible; must not be removed */}
      <div
        className="flex items-start gap-3 rounded-lg border border-yellow-500/30 bg-yellow-500/5 px-4 py-3"
        role="status"
        aria-label="Email delivery status"
      >
        <span className="mt-0.5 shrink-0 text-yellow-400" aria-hidden="true">
          <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4">
            <path
              fillRule="evenodd"
              d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 5a.75.75 0 01.75.75v3.5a.75.75 0 01-1.5 0v-3.5A.75.75 0 0110 5zm0 9a1 1 0 100-2 1 1 0 000 2z"
              clipRule="evenodd"
            />
          </svg>
        </span>
        <div>
          <p className="text-xs font-semibold text-yellow-400">Dry-Run Active — No Emails Are Being Sent</p>
          <p className="text-[11px] text-text-muted mt-0.5 leading-relaxed">
            Real email delivery is disabled (
            <code className="font-mono">ALERT_EMAIL_DRY_RUN=true</code>
            ). Signals are logged and visible here, but nothing is delivered externally. Email
            delivery activates only after Resend domain verification is complete.
          </p>
        </div>
      </div>

      {/* Review Queue — Alert Candidates */}
      <SectionCard title="Watchtower Signals">
        {candLoading ? (
          <InlineLoader text="Loading signals…" />
        ) : candError ? (
          <div className="px-4 py-6 text-center">
            <p className="text-xs text-red-400">
              Could not load signals. The backend may be unavailable.
            </p>
          </div>
        ) : !candidates || candidates.length === 0 ? (
          <EmptyState
            title="No signals in the queue"
            description="Signals appear here when Watchtower detects a meaningful Intel threshold crossing — such as a new Buy recommendation or a conviction upgrade. Run Intel to generate fresh signals."
          />
        ) : (
          <div>
            {candidates.map((c) => (
              <CandidateRow key={c.id} c={c} />
            ))}
          </div>
        )}
      </SectionCard>

      {/* Delivery Outbox — read-only status visibility */}
      <SectionCard title="Delivery Status">
        {outboxLoading ? (
          <InlineLoader text="Loading delivery status…" />
        ) : outboxError ? (
          <div className="px-4 py-6 text-center">
            <p className="text-xs text-red-400">
              Could not load delivery status. The backend may be unavailable.
            </p>
          </div>
        ) : !outbox || outbox.length === 0 ? (
          <EmptyState
            title="No delivery records yet"
            description="Once signals are promoted for delivery, their status appears here. Dry-run entries show as 'Dry-Run Only' rather than 'Sent'."
          />
        ) : (
          <div>
            {outbox.map((o) => (
              <OutboxRow key={o.id} o={o} />
            ))}
          </div>
        )}
      </SectionCard>
    </div>
  );
}
