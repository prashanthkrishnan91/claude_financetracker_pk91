"use client";

/**
 * Radar — Opportunity Study Room (Stage 4G).
 *
 * Honest Coming-Later destination.
 * No mock candidates. No fake workflow chips populated with data.
 * Stage 6G activates real candidates (depends on Stage 5L Radar worker).
 */

export default function RadarPage() {
  return (
    <div className="max-w-2xl mx-auto px-4 py-6">
      {/* Header */}
      <div className="mb-8">
        <p className="text-[10px] font-mono uppercase tracking-[0.14em] text-accent opacity-50">
          Opportunity Study Room
        </p>
        <h1 className="text-xl font-semibold text-text-primary tracking-tight mt-0.5">
          Radar
        </h1>
      </div>

      {/* Honest Coming-Later state */}
      <div className="rounded-xl border border-dashed border-border bg-surface/40 px-6 py-10 text-center">
        <div className="mb-3 text-text-muted opacity-30">
          {/* Radar icon */}
          <svg
            viewBox="0 0 48 48"
            fill="none"
            className="w-12 h-12 mx-auto"
            aria-hidden="true"
          >
            <circle cx="24" cy="24" r="22" stroke="currentColor" strokeWidth="1.5" />
            <circle cx="24" cy="24" r="14" stroke="currentColor" strokeWidth="1" strokeDasharray="3 3" />
            <circle cx="24" cy="24" r="6" stroke="currentColor" strokeWidth="1" />
            <line x1="24" y1="2" x2="24" y2="46" stroke="currentColor" strokeWidth="1" />
            <line x1="2" y1="24" x2="46" y2="24" stroke="currentColor" strokeWidth="1" />
          </svg>
        </div>
        <p className="text-sm font-medium text-text-secondary leading-snug">
          Radar is being prepared.
        </p>
        <p className="text-xs text-text-muted mt-1.5 leading-relaxed max-w-xs mx-auto">
          The next intelligence stage will surface opportunities here. Candidates will appear once the
          opportunity screening layer is live — built from deterministic sector and valuation screens,
          not trending tickers or social signals.
        </p>
      </div>

      {/* Capability note */}
      <div className="mt-6 rounded-lg border border-border/50 bg-surface px-4 py-3.5">
        <p className="text-[10px] font-semibold uppercase tracking-widest text-text-muted mb-1.5">
          What Radar will surface
        </p>
        <ul className="space-y-1.5 text-xs text-text-muted leading-snug">
          <li className="flex items-start gap-2">
            <span className="mt-0.5 shrink-0 text-border-strong">—</span>
            <span>
              Candidates not yet owned that pass a deterministic sector and valuation screen — not
              trending tickers.
            </span>
          </li>
          <li className="flex items-start gap-2">
            <span className="mt-0.5 shrink-0 text-border-strong">—</span>
            <span>
              Each candidate surfaced with a thesis quality rating and evidence band — no hollow
              &quot;opportunity&quot; labels.
            </span>
          </li>
          <li className="flex items-start gap-2">
            <span className="mt-0.5 shrink-0 text-border-strong">—</span>
            <span>
              Reachable from the Today secondary rail once the screening layer is live.
            </span>
          </li>
        </ul>
      </div>
    </div>
  );
}
