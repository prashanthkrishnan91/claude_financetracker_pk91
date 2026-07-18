"use client";

import { useState } from "react";
import { cn, formatCurrency } from "@/lib/utils";
import {
  useWatchlist,
  useAddWatchlistItem,
  useDeleteWatchlistItem,
} from "@/lib/hooks";
import { InlineLoader, Spinner } from "@/components/ui/Spinner";
import { EmptyState } from "@/components/ui/EmptyState";
import {
  criteriaTypeLabel,
  criteriaStatus,
  criteriaStatusLabel,
} from "@/lib/watchlist";
import type { WatchlistCriteriaType, WatchlistItem } from "@/lib/api";

// ── Watchlist panel — add form + entry list ───────────────────────────────────

export function WatchlistPanel() {
  const { data: items, isLoading, error } = useWatchlist();

  return (
    <div className="space-y-4">
      <AddWatchlistForm />

      {isLoading && <InlineLoader text="Loading watchlist…" />}

      {!isLoading && !!error && (
        <EmptyState
          title="Could not load your watchlist"
          description="Check your connection and try again."
        />
      )}

      {!isLoading && !error && (!items || items.length === 0) && (
        <EmptyState
          title="Nothing on your watchlist yet"
          description="Add a ticker above with a price you care about, and it will show up here."
        />
      )}

      {!isLoading && !error && items && items.length > 0 && (
        <section className="card-glass overflow-hidden">
          <div className="px-5 pt-5 pb-3">
            <p className="text-[10px] uppercase tracking-label text-text-muted opacity-60">
              Watching · {items.length} ticker{items.length !== 1 ? "s" : ""}
            </p>
          </div>
          <div className="divide-y divide-border/40">
            {items.map(item => (
              <WatchlistRow key={item.id} item={item} />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

// ── Add form ──────────────────────────────────────────────────────────────────

function AddWatchlistForm() {
  const addItem = useAddWatchlistItem();
  const [ticker, setTicker] = useState("");
  const [criteriaType, setCriteriaType] = useState<WatchlistCriteriaType>("price_below");
  const [threshold, setThreshold] = useState("");
  const [notes, setNotes] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);

    const cleanTicker = ticker.trim().toUpperCase();
    const parsedThreshold = Number(threshold);
    if (!cleanTicker) {
      setFormError("Enter a ticker symbol.");
      return;
    }
    if (!Number.isFinite(parsedThreshold) || parsedThreshold <= 0) {
      setFormError("Enter a price above zero.");
      return;
    }

    try {
      await addItem.mutateAsync({
        ticker: cleanTicker,
        criteria_type: criteriaType,
        threshold: parsedThreshold,
        notes: notes.trim() || undefined,
      });
      setTicker("");
      setThreshold("");
      setNotes("");
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Could not add this ticker.");
    }
  };

  return (
    <form onSubmit={handleSubmit} className="card-glass p-5 space-y-3">
      <p className="text-[10px] uppercase tracking-label text-text-muted opacity-60">
        Add a ticker to watch
      </p>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <label className="block">
          <span className="text-[11px] text-text-muted">Ticker</span>
          <input
            type="text"
            value={ticker}
            onChange={e => setTicker(e.target.value)}
            placeholder="e.g. AAPL"
            autoCapitalize="characters"
            className="mt-1 w-full rounded-md bg-surface-elevated border border-border px-3 py-2 text-sm text-text-primary placeholder:text-text-muted/60 focus:outline-none focus:ring-2 focus:ring-accent/60"
          />
        </label>

        <label className="block">
          <span className="text-[11px] text-text-muted">Alert me when</span>
          <select
            value={criteriaType}
            onChange={e => setCriteriaType(e.target.value as WatchlistCriteriaType)}
            className="mt-1 w-full rounded-md bg-surface-elevated border border-border px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent/60"
          >
            <option value="price_below">Price at or below</option>
            <option value="price_above">Price at or above</option>
          </select>
        </label>

        <label className="block">
          <span className="text-[11px] text-text-muted">Price (USD)</span>
          <input
            type="number"
            inputMode="decimal"
            min="0"
            step="0.01"
            value={threshold}
            onChange={e => setThreshold(e.target.value)}
            placeholder="e.g. 150.00"
            className="mt-1 w-full rounded-md bg-surface-elevated border border-border px-3 py-2 text-sm text-text-primary placeholder:text-text-muted/60 focus:outline-none focus:ring-2 focus:ring-accent/60"
          />
        </label>
      </div>

      <label className="block">
        <span className="text-[11px] text-text-muted">Note (optional)</span>
        <input
          type="text"
          value={notes}
          onChange={e => setNotes(e.target.value)}
          placeholder="Why you're watching this"
          className="mt-1 w-full rounded-md bg-surface-elevated border border-border px-3 py-2 text-sm text-text-primary placeholder:text-text-muted/60 focus:outline-none focus:ring-2 focus:ring-accent/60"
        />
      </label>

      {formError && <p className="text-[11px] text-action-sell">{formError}</p>}

      <div className="flex items-center gap-3">
        <button
          type="submit"
          disabled={addItem.isPending}
          className="inline-flex items-center gap-2 rounded-md bg-accent/10 border border-accent/30 text-accent px-4 py-2 text-sm font-medium hover:bg-accent/15 transition-colors disabled:opacity-50"
        >
          {addItem.isPending && <Spinner className="h-3.5 w-3.5" />}
          Add to watchlist
        </button>
      </div>
    </form>
  );
}

// ── Row ───────────────────────────────────────────────────────────────────────

function WatchlistRow({ item }: { item: WatchlistItem }) {
  const deleteItem = useDeleteWatchlistItem();
  const status = criteriaStatus(item);

  return (
    <div className="px-5 py-3.5 flex items-start justify-between gap-3">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2 mb-0.5">
          <span className="ticker-symbol text-sm">{item.ticker}</span>
          {status === "met" && (
            <span className="text-[9px] px-1.5 py-0.5 rounded border font-semibold uppercase tracking-wide bg-action-buy/10 text-action-buy border-action-buy/20">
              {criteriaStatusLabel("met")}
            </span>
          )}
          {status === "unknown" && (
            <span className="text-[9px] px-1.5 py-0.5 rounded border uppercase tracking-wide bg-surface-elevated text-text-muted border-border">
              {criteriaStatusLabel("unknown")}
            </span>
          )}
        </div>
        <p className="text-[11px] text-text-secondary leading-snug">
          {criteriaTypeLabel(item.criteria_type)} {formatCurrency(item.threshold)}
        </p>
        <p className="text-[11px] text-text-muted leading-snug mt-0.5">
          Current price:{" "}
          {item.current_price != null ? formatCurrency(item.current_price) : "—"}
          {status === "not_met" && (
            <span className="opacity-70"> · {criteriaStatusLabel("not_met")}</span>
          )}
        </p>
        {item.notes && (
          <p className="text-[11px] text-text-muted italic mt-1 leading-snug">{item.notes}</p>
        )}
      </div>

      <button
        onClick={() => deleteItem.mutate(item.id)}
        disabled={deleteItem.isPending}
        aria-label={`Remove ${item.ticker} from watchlist`}
        className={cn(
          "shrink-0 text-[11px] px-2 py-1 rounded border border-border text-text-muted",
          "hover:text-action-sell hover:border-action-sell/30 transition-colors disabled:opacity-50"
        )}
      >
        {deleteItem.isPending ? "Removing…" : "Remove"}
      </button>
    </div>
  );
}
