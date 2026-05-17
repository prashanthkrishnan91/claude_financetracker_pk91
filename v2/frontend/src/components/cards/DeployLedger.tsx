"use client";

/**
 * Deploy Ledger UI components — Stage 4E.
 *
 * Capital Allocation Ledger: calm boutique design for a single amateur investor.
 * Uses existing Stage 4 design tokens and primitives.
 * No invented intelligence — all displayed data comes from Deploy v3 APIs only.
 */

import { cn, formatCurrency } from "@/lib/utils";
import { ComingLaterPanel } from "@/components/cards/IntelV3Primitives";
import type {
  LedgerItem,
  LedgerPlanState,
  LedgerPlanSeverity,
  GuardrailGroup,
  LedgerStatusGroup,
} from "@/lib/deploy-ledger";

// ── Plan state badge ──────────────────────────────────────────────────────────

const SEVERITY_STYLES: Record<LedgerPlanSeverity, string> = {
  ok:          "bg-action-buy/10 text-action-buy border-action-buy/25",
  pending:     "bg-action-hold/10 text-action-hold border-action-hold/25",
  caution:     "bg-action-trim/10 text-action-trim border-action-trim/25",
  blocked:     "bg-action-sell/10 text-action-sell border-action-sell/25",
  unavailable: "bg-surface-elevated text-text-muted border-border",
};

export function LedgerPlanStateBadge({
  planState,
  className,
}: {
  planState: LedgerPlanState;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center text-[10px] px-2 py-0.5 rounded-pill border font-semibold tracking-wide uppercase",
        SEVERITY_STYLES[planState.severity],
        className,
      )}
    >
      {planState.headline}
    </span>
  );
}

// ── Action badge ──────────────────────────────────────────────────────────────

const ACTION_STYLES: Record<string, string> = {
  BUY:  "bg-action-buy/10 text-action-buy border-action-buy/30",
  TRIM: "bg-action-trim/10 text-action-trim border-action-trim/30",
  SELL: "bg-action-sell/10 text-action-sell border-action-sell/30",
  HOLD: "bg-action-hold/10 text-action-hold border-action-hold/25",
};

function ActionBadge({ action }: { action: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center text-[10px] px-2 py-0.5 rounded border font-bold tracking-widest uppercase",
        ACTION_STYLES[action] ?? ACTION_STYLES.HOLD,
      )}
    >
      {action}
    </span>
  );
}

// ── Guardrail status pill ─────────────────────────────────────────────────────

const STATUS_GROUP_STYLES: Record<LedgerStatusGroup, string> = {
  actionable:       "text-action-buy",
  pending:          "text-action-hold",
  informational:    "text-text-secondary",
  blocked:          "text-action-sell",
  suppressed:       "text-action-trim",
  not_ready:        "text-text-muted",
  not_evaluated_yet: "text-text-muted",
  unknown:          "text-text-muted",
};

function StatusPill({ item }: { item: LedgerItem }) {
  const cls = STATUS_GROUP_STYLES[item.ledgerStatus.group] ?? "text-text-muted";
  return (
    <span className={cn("text-[10px] font-medium", cls)}>
      {item.ledgerStatus.label}
    </span>
  );
}

// ── Action card (mobile-first) ────────────────────────────────────────────────

export function LedgerActionCard({
  item,
  className,
}: {
  item: LedgerItem;
  className?: string;
}) {
  const hasDollar = item.dollarAmount != null && item.dollarAmount > 0;

  return (
    <div
      className={cn(
        "rounded-md border bg-surface/40 p-3.5 space-y-2 transition-colors",
        item.action === "BUY"  ? "border-action-buy/20" :
        item.action === "TRIM" ? "border-action-trim/20" :
        item.action === "SELL" ? "border-action-sell/20" :
                                  "border-border/60",
        className,
      )}
    >
      {/* Top row: ticker + action + amount */}
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2">
          <span className="font-mono font-bold text-sm text-text-primary">{item.ticker}</span>
          <ActionBadge action={item.action} />
        </div>
        <div className="flex items-center gap-2">
          {hasDollar ? (
            <span className={cn(
              "font-mono font-semibold text-sm tabular-nums",
              item.action === "BUY"  ? "text-action-buy" :
              item.action === "TRIM" ? "text-action-trim" :
              item.action === "SELL" ? "text-action-sell" :
                                       "text-text-secondary",
            )}>
              {formatCurrency(item.dollarAmount!)}
            </span>
          ) : (
            <span className="text-[11px] text-text-muted font-mono">—</span>
          )}
          <StatusPill item={item} />
        </div>
      </div>

      {/* Rationale: "why this dollar goes here" */}
      {item.rationale && (
        <p className="text-[11px] text-text-secondary leading-snug">
          {item.rationale}
        </p>
      )}

      {/* Status detail */}
      <p className="text-[10px] text-text-muted leading-snug">
        {item.ledgerStatus.detail}
      </p>
    </div>
  );
}

// ── Guardrail status rail ─────────────────────────────────────────────────────

const GROUP_HEADER_STYLES: Record<LedgerStatusGroup, string> = {
  actionable:       "text-action-buy",
  pending:          "text-action-hold",
  informational:    "text-text-secondary",
  blocked:          "text-action-sell",
  suppressed:       "text-action-trim",
  not_ready:        "text-text-muted",
  not_evaluated_yet: "text-text-muted",
  unknown:          "text-text-muted",
};

function GuardrailGroupSection({ group }: { group: GuardrailGroup }) {
  const headerCls = GROUP_HEADER_STYLES[group.group] ?? "text-text-muted";
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <span className={cn("text-[10px] font-semibold uppercase tracking-widest", headerCls)}>
          {group.displayLabel}
        </span>
        <span className="text-[10px] text-text-muted">
          {group.items.length} {group.items.length === 1 ? "position" : "positions"}
        </span>
      </div>
      <p className="text-[10px] text-text-muted leading-snug">{group.explanation}</p>
      <div className="flex flex-wrap gap-1.5">
        {group.items.map((item) => (
          <div
            key={item.ticker}
            className="flex items-center gap-1.5 px-2 py-1 rounded border border-border/60 bg-surface-elevated/40"
          >
            <span className="font-mono text-[11px] font-semibold text-text-primary">{item.ticker}</span>
            <ActionBadge action={item.action} />
            {item.dollarAmount != null && item.dollarAmount > 0 && (
              <span className="font-mono text-[10px] text-text-secondary tabular-nums">
                {formatCurrency(item.dollarAmount)}
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

export function GuardrailStatusRail({
  groups,
  className,
}: {
  groups: GuardrailGroup[];
  className?: string;
}) {
  if (groups.length === 0) return null;
  return (
    <div className={cn("space-y-4", className)}>
      <p className="text-[10px] font-semibold uppercase tracking-widest text-text-muted">
        Guardrail status
      </p>
      {groups.map((group) => (
        <GuardrailGroupSection key={group.group} group={group} />
      ))}
    </div>
  );
}

// ── Portfolio shape preview ───────────────────────────────────────────────────

export function PortfolioShapePreview({ className }: { className?: string }) {
  return (
    <ComingLaterPanel
      title="Portfolio shape preview"
      caption="Before / after allocation weights will appear here once the Deploy v3 pipeline carries per-position weight data. Coming in Stage 5."
      className={className}
    />
  );
}

// ── Coming later section ──────────────────────────────────────────────────────

export function ComingLaterLedgerSection({ className }: { className?: string }) {
  return (
    <div className={cn("space-y-2", className)}>
      <p className="text-[10px] font-semibold uppercase tracking-widest text-text-muted">
        Coming later
      </p>
      <div className="grid gap-2 sm:grid-cols-2">
        <ComingLaterPanel
          title="Tax-lot optimization"
          caption="Specific lot selection and tax-impact estimates. Coming in Stage 5."
        />
        <ComingLaterPanel
          title="Wash-sale protection"
          caption="Wash-sale window detection and cost-basis tracking. Coming in Stage 5."
        />
        <ComingLaterPanel
          title="Canonical target allocation"
          caption="Optimizer-driven target weights tied to a live allocation model. Coming in Stage 5."
        />
        <ComingLaterPanel
          title="Execution intelligence"
          caption="Slippage estimates, timing guidance, and broker routing context. Coming in Stage 6."
        />
      </div>
    </div>
  );
}

// ── Cash planning strip ───────────────────────────────────────────────────────

export function CashPlanningStrip({
  amount,
  onChange,
  availableCash,
  presets = [500, 900, 1500, 2000],
  className,
}: {
  amount: number;
  onChange: (v: number) => void;
  availableCash?: number | null;
  presets?: number[];
  className?: string;
}) {
  return (
    <section
      aria-label="Planning capital"
      className={cn(
        "card-glass p-4 space-y-3 border border-border/80",
        className,
      )}
    >
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-widest text-text-muted">
            Planning capital
          </p>
          <p className="text-[10px] text-text-muted mt-0.5">
            Enter the cash you are planning to deploy. Not broker-verified — planning use only.
          </p>
        </div>
        {availableCash != null && (
          <p className="text-[10px] text-text-secondary">
            Available:{" "}
            <span className="font-mono text-text-primary font-semibold">
              {formatCurrency(availableCash)}
            </span>
          </p>
        )}
      </div>

      <div className="flex gap-3 items-center">
        <div className="relative flex-1 min-w-0">
          <span className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted text-sm select-none">$</span>
          <input
            type="number"
            value={amount}
            onChange={(e) => onChange(Math.max(0, Number(e.target.value)))}
            className="w-full pl-7 pr-3 py-2 bg-surface border border-border/80 rounded-md text-text-primary font-mono text-base focus:outline-none focus:ring-1 focus:ring-accent"
            min={0}
            step={50}
            aria-label="Planning capital amount"
          />
        </div>
      </div>

      <div className="flex gap-2 flex-wrap">
        {presets.map((preset) => (
          <button
            key={preset}
            type="button"
            onClick={() => onChange(preset)}
            className={cn(
              "px-3 py-1 text-xs rounded border transition-colors",
              amount === preset
                ? "bg-accent text-background font-semibold border-accent"
                : "text-text-muted bg-surface-elevated border-border/70 hover:text-text-primary",
            )}
          >
            ${preset}
          </button>
        ))}
      </div>
    </section>
  );
}

// ── Ledger plan summary bar ───────────────────────────────────────────────────

export function LedgerPlanSummaryBar({
  planState,
  cashToDeploy,
  amountAware,
  className,
}: {
  planState: LedgerPlanState;
  cashToDeploy?: number | null;
  amountAware?: boolean;
  className?: string;
}) {
  const barStyles: Record<LedgerPlanSeverity, string> = {
    ok:          "border-action-buy/25 bg-action-buy/5",
    pending:     "border-action-hold/25 bg-action-hold/5",
    caution:     "border-action-trim/25 bg-action-trim/5",
    blocked:     "border-action-sell/25 bg-action-sell/5",
    unavailable: "border-border/60 bg-surface-elevated/30",
  };

  return (
    <div className={cn(
      "rounded-md border px-4 py-3 space-y-1",
      barStyles[planState.severity],
      className,
    )}>
      <div className="flex items-center gap-2 flex-wrap">
        <LedgerPlanStateBadge planState={planState} />
        {amountAware && cashToDeploy != null && (
          <span className="text-[10px] text-text-secondary">
            Planned: <span className="font-mono font-semibold text-text-primary">{formatCurrency(cashToDeploy)}</span>
          </span>
        )}
      </div>
      <p className="text-[11px] text-text-secondary leading-snug">{planState.sub}</p>
    </div>
  );
}

// ── Non-brokerage disclaimer ──────────────────────────────────────────────────

export function NonBrokerageDisclaimer({ className }: { className?: string }) {
  return (
    <p className={cn("text-[10px] text-text-muted leading-snug", className)}>
      This is a planning tool — not a brokerage account, not financial advice, and not broker-executed.
      Intel v3 policy owns all Buy / Hold / Trim / Sell authority. No real trades occur here.
    </p>
  );
}
