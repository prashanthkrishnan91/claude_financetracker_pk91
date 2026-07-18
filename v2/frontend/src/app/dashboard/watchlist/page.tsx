"use client";

/**
 * Watchlist — primary informational view.
 * Add tickers with one deterministic price criterion, see current price and
 * criterion status, edit or delete entries. Purely informational: no buy
 * actions and no advisor links. Handles duplicate (409) and
 * migration-required (503) backend states explicitly.
 */

import { useState } from "react";
import { cn } from "@/lib/utils";
import { PageLoader } from "@/components/ui/Spinner";
import { EmptyState } from "@/components/ui/EmptyState";
import { WatchlistEntryCard } from "@/components/watchlist/WatchlistEntryCard";
import {
  useWatchlist,
  useCreateWatchlistItem,
  validateWatchlistInput,
  normalizeTicker,
  criteriaTypeLabel,
  isMigrationRequiredError,
  isDuplicateEntryError,
  type WatchlistCriteriaType,
  type WatchlistFormErrors,
} from "@/lib/watchlist";

const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60";

const FIELD_CLASS =
  "w-full bg-surface-elevated border border-border rounded-md px-3 py-2 text-sm text-text-primary " +
  "placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-accent/60 focus:border-accent/50";

export default function WatchlistPage() {
  const { data: items, isLoading, error, refetch } = useWatchlist();

  const migrationRequired = isMigrationRequiredError(error);

  return (
    <>
      <header className="page-header">
        <div className="page-header-inner">
          <div>
            <h1 className="text-xl font-display text-text-primary">Watchlist</h1>
            <p className="text-[10px] uppercase tracking-label text-text-muted opacity-60 leading-none mt-0.5">
              Informational only — no trading actions
            </p>
          </div>
        </div>
      </header>

      <main className="page-main">
        {isLoading && <PageLoader />}

        {!isLoading && migrationRequired && (
          <div className="info-block" role="status">
            <p className="text-sm font-semibold text-text-primary mb-1">
              Watchlist needs a one-time database migration
            </p>
            <p className="text-xs text-text-secondary leading-relaxed">
              {error instanceof Error ? error.message : "The Watchlist table has not been created yet."}
            </p>
            <button
              type="button"
              onClick={() => refetch()}
              className={cn("btn-secondary min-h-[40px] mt-3", FOCUS_RING)}
            >
              Retry
            </button>
          </div>
        )}

        {!isLoading && error && !migrationRequired && (
          <EmptyState
            title="Failed to load watchlist"
            description={error instanceof Error ? error.message : "Something went wrong."}
            action={
              <button
                type="button"
                onClick={() => refetch()}
                className={cn("btn-secondary min-h-[40px]", FOCUS_RING)}
              >
                Retry
              </button>
            }
          />
        )}

        {!isLoading && !error && (
          <>
            <AddWatchlistForm />

            {(!items || items.length === 0) ? (
              <EmptyState
                title="Your watchlist is empty"
                description="Add a ticker you're watching and the price level you care about."
              />
            ) : (
              <section aria-label="Watchlist entries">
                <h2 className="section-header mb-3">
                  Watching · {items.length} entr{items.length !== 1 ? "ies" : "y"}
                </h2>
                <ul className="grid grid-cols-1 md:grid-cols-2 gap-3 list-none p-0">
                  {items.map(item => (
                    <WatchlistEntryCard key={item.id} item={item} />
                  ))}
                </ul>
              </section>
            )}
          </>
        )}
      </main>
    </>
  );
}

// ── Add form ──────────────────────────────────────────────────────────────────

function AddWatchlistForm() {
  const [ticker, setTicker] = useState("");
  const [criteriaType, setCriteriaType] = useState<WatchlistCriteriaType>("price_below");
  const [threshold, setThreshold] = useState("");
  const [notes, setNotes] = useState("");
  const [errors, setErrors] = useState<WatchlistFormErrors>({});
  const [lastAdded, setLastAdded] = useState<string | null>(null);

  const createMutation = useCreateWatchlistItem();

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setLastAdded(null);
    const validation = validateWatchlistInput({ ticker, threshold, notes });
    setErrors(validation);
    if (Object.keys(validation).length > 0) return;

    const normalizedTicker = normalizeTicker(ticker);
    createMutation.mutate(
      {
        ticker: normalizedTicker,
        criteria_type: criteriaType,
        threshold: Number(threshold),
        notes: notes.trim() ? notes.trim() : null,
      },
      {
        onSuccess: () => {
          setLastAdded(normalizedTicker);
          setTicker("");
          setThreshold("");
          setNotes("");
          setErrors({});
        },
      }
    );
  };

  const submitError = createMutation.isError
    ? createMutation.error instanceof Error
      ? createMutation.error.message
      : "Failed to add entry."
    : null;

  return (
    <section aria-label="Add to watchlist" className="data-card p-4">
      <h2 className="section-header mb-3">Add a ticker</h2>
      <form onSubmit={handleSubmit} noValidate className="space-y-3">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          <div>
            <label htmlFor="wl-ticker" className="metric-label block mb-1">
              Ticker
            </label>
            <input
              id="wl-ticker"
              type="text"
              autoComplete="off"
              autoCapitalize="characters"
              placeholder="e.g. VTI"
              value={ticker}
              onChange={e => setTicker(e.target.value.toUpperCase())}
              aria-invalid={!!errors.ticker}
              aria-describedby={errors.ticker ? "wl-ticker-err" : undefined}
              className={cn(FIELD_CLASS, "font-mono uppercase")}
            />
            {errors.ticker && (
              <p id="wl-ticker-err" className="text-xs text-negative mt-1">
                {errors.ticker}
              </p>
            )}
          </div>

          <div>
            <label htmlFor="wl-criteria" className="metric-label block mb-1">
              Criterion
            </label>
            <select
              id="wl-criteria"
              value={criteriaType}
              onChange={e => setCriteriaType(e.target.value as WatchlistCriteriaType)}
              className={FIELD_CLASS}
            >
              <option value="price_below">{criteriaTypeLabel("price_below")}</option>
              <option value="price_above">{criteriaTypeLabel("price_above")}</option>
            </select>
          </div>

          <div>
            <label htmlFor="wl-threshold" className="metric-label block mb-1">
              Threshold (USD)
            </label>
            <input
              id="wl-threshold"
              type="number"
              inputMode="decimal"
              step="any"
              min="0"
              placeholder="e.g. 215.00"
              value={threshold}
              onChange={e => setThreshold(e.target.value)}
              aria-invalid={!!errors.threshold}
              aria-describedby={errors.threshold ? "wl-threshold-err" : undefined}
              className={FIELD_CLASS}
            />
            {errors.threshold && (
              <p id="wl-threshold-err" className="text-xs text-negative mt-1">
                {errors.threshold}
              </p>
            )}
          </div>

          <div>
            <label htmlFor="wl-notes" className="metric-label block mb-1">
              Note (optional)
            </label>
            <input
              id="wl-notes"
              type="text"
              placeholder="Why you're watching"
              value={notes}
              onChange={e => setNotes(e.target.value)}
              maxLength={500}
              aria-invalid={!!errors.notes}
              aria-describedby={errors.notes ? "wl-notes-err" : undefined}
              className={FIELD_CLASS}
            />
            {errors.notes && (
              <p id="wl-notes-err" className="text-xs text-negative mt-1">
                {errors.notes}
              </p>
            )}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <button
            type="submit"
            disabled={createMutation.isPending}
            className={cn("btn-primary min-h-[40px]", FOCUS_RING)}
          >
            {createMutation.isPending ? "Adding…" : "Add to watchlist"}
          </button>

          {/* Async submit result — announced politely to screen readers */}
          <div aria-live="polite" className="min-w-0">
            {submitError && (
              <p className="text-xs text-negative">
                {isDuplicateEntryError(createMutation.error)
                  ? submitError
                  : isMigrationRequiredError(createMutation.error)
                    ? `Watchlist table missing — ${submitError}`
                    : submitError}
              </p>
            )}
            {lastAdded && !submitError && (
              <p className="text-xs text-accent">Added {lastAdded} to your watchlist.</p>
            )}
          </div>
        </div>
      </form>
    </section>
  );
}
