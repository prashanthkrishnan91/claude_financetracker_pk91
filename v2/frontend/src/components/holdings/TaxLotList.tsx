"use client";

import { useState } from "react";
import { cn, formatCurrency } from "@/lib/utils";
import { useTaxLots } from "@/lib/hooks";
import { InlineLoader } from "@/components/ui/Spinner";
import type { TaxLot, TaxLotsTickerEntry } from "@/lib/api";

// ── Tax lots — per-ticker expandable breakdown ────────────────────────────────
//
// Shows every purchase lot with its tax status. Missing market data renders
// as "—" — values are never fabricated from cost basis.

export function TaxLotList() {
  const { data, isLoading, error } = useTaxLots();

  const tickers = data ? Object.keys(data.tickers).sort() : [];

  return (
    <section className="card-glass overflow-hidden">
      <div className="px-5 pt-5 pb-3">
        <p className="text-[10px] uppercase tracking-label text-text-muted opacity-60">
          Tax Lots
        </p>
        <p className="text-[11px] text-text-muted mt-1 leading-snug">
          Each purchase is taxed on its own clock. Lots held{" "}
          {data ? `${data.long_term_holding_days} days` : "a year"} or more qualify
          for the lower long-term rate.
        </p>
      </div>

      {isLoading && <InlineLoader text="Loading tax lots…" />}

      {!isLoading && !!error && (
        <p className="px-5 pb-5 text-sm text-text-muted italic">
          Could not load tax lot data. Check your connection and try again.
        </p>
      )}

      {!isLoading && !error && tickers.length === 0 && (
        <p className="px-5 pb-5 text-sm text-text-muted italic">
          No tax lot data yet — lots appear once transactions are imported.
        </p>
      )}

      {!isLoading && !error && tickers.length > 0 && (
        <div className="divide-y divide-border/40">
          {tickers.map(ticker => (
            <TickerLotGroup key={ticker} ticker={ticker} entry={data!.tickers[ticker]} />
          ))}
        </div>
      )}
    </section>
  );
}

// ── Per-ticker group (expandable) ─────────────────────────────────────────────

function TickerLotGroup({ ticker, entry }: { ticker: string; entry: TaxLotsTickerEntry }) {
  const [open, setOpen] = useState(false);
  const s = entry.summary;

  return (
    <div>
      <button
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        aria-label={`${open ? "Hide" : "Show"} tax lots for ${ticker}`}
        className="w-full text-left px-5 py-3.5 hover:bg-surface-elevated/40 transition-colors"
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2 mb-0.5">
              <span className="ticker-symbol text-sm">{ticker}</span>
              <span className="text-[10px] text-text-muted">
                {s.lot_count} lot{s.lot_count !== 1 ? "s" : ""}
                {s.long_term_lot_count > 0 && ` · ${s.long_term_lot_count} long-term`}
                {s.short_term_lot_count > 0 && ` · ${s.short_term_lot_count} short-term`}
              </span>
            </div>
            {s.next_long_term_countdown_days != null && s.short_term_lot_count > 0 && (
              <p className="text-[10px] text-warning leading-snug">
                Next lot turns long-term in {s.next_long_term_countdown_days} day
                {s.next_long_term_countdown_days !== 1 ? "s" : ""}
              </p>
            )}
          </div>
          <div className="text-right shrink-0">
            <p className="data-value text-sm">
              {s.unrealized_gain_total != null
                ? formatCurrency(s.unrealized_gain_total)
                : "—"}
            </p>
            <p className="text-[10px] text-text-muted">
              unrealized · cost {formatCurrency(s.total_cost_basis)}
            </p>
          </div>
        </div>
      </button>

      {open && (
        <div className="px-5 pb-4 overflow-x-auto">
          <table className="w-full text-left min-w-[560px]">
            <thead>
              <tr className="text-[10px] uppercase tracking-label text-text-muted opacity-60">
                <th className="py-1.5 pr-3 font-medium">Bought</th>
                <th className="py-1.5 pr-3 font-medium text-right">Shares</th>
                <th className="py-1.5 pr-3 font-medium text-right">Cost basis</th>
                <th className="py-1.5 pr-3 font-medium text-right">Unrealized gain</th>
                <th className="py-1.5 font-medium">Tax status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/30">
              {entry.lots.map((lot, i) => (
                <LotRow key={`${lot.acquired_date}-${i}`} lot={lot} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function LotRow({ lot }: { lot: TaxLot }) {
  return (
    <tr className="text-xs">
      <td className="py-2 pr-3 text-text-secondary whitespace-nowrap">
        {formatLotDate(lot.acquired_date)}
      </td>
      <td className="py-2 pr-3 text-right font-mono tabular-nums text-text-primary">
        {formatQuantity(lot.quantity)}
      </td>
      <td className="py-2 pr-3 text-right font-mono tabular-nums text-text-primary">
        {formatCurrency(lot.cost_basis)}
      </td>
      <td className="py-2 pr-3 text-right font-mono tabular-nums">
        {lot.unrealized_gain != null ? (
          <span className={lot.unrealized_gain >= 0 ? "text-action-buy" : "text-action-sell"}>
            {formatCurrency(lot.unrealized_gain)}
            {lot.unrealized_gain_pct != null && (
              <span className="opacity-70">
                {" "}({lot.unrealized_gain_pct >= 0 ? "+" : ""}
                {lot.unrealized_gain_pct.toFixed(1)}%)
              </span>
            )}
          </span>
        ) : (
          <span className="text-text-muted">—</span>
        )}
      </td>
      <td className="py-2">
        <TaxStatusBadge lot={lot} />
      </td>
    </tr>
  );
}

// ── Tax status badge — Long-term green / Short-term amber + countdown ─────────

function TaxStatusBadge({ lot }: { lot: TaxLot }) {
  if (lot.is_long_term) {
    return (
      <span className="text-[9px] px-1.5 py-0.5 rounded border font-semibold uppercase tracking-wide bg-action-buy/10 text-action-buy border-action-buy/20">
        Long-term
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
      <span className="text-[9px] px-1.5 py-0.5 rounded border font-semibold uppercase tracking-wide bg-warning/10 text-warning border-warning/20">
        Short-term
      </span>
      {lot.days_until_long_term != null && (
        <span className="text-[10px] text-text-muted">
          {lot.days_until_long_term} day{lot.days_until_long_term !== 1 ? "s" : ""} to long-term
        </span>
      )}
    </span>
  );
}

// ── Formatters ────────────────────────────────────────────────────────────────

function formatLotDate(iso: string): string {
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso || "—";
    return d.toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return iso || "—";
  }
}

function formatQuantity(q: number): string {
  return Number.isInteger(q) ? String(q) : q.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
}
