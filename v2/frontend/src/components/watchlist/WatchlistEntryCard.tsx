"use client";

/**
 * Single watchlist entry card — display, edit-in-place (criterion/threshold/
 * note via PATCH) and delete with inline confirm. Purely informational: no
 * buy actions, no advisor links.
 */

import { useEffect, useRef, useState } from "react";
import { cn, formatCurrency } from "@/lib/utils";
import { relativeAgeLabel } from "@/lib/positions-view";
import {
  useUpdateWatchlistItem,
  useDeleteWatchlistItem,
  validateWatchlistInput,
  criteriaTypeLabel,
  formatCriteriaSentence,
  criteriaStatus,
  criteriaStatusLabel,
  type WatchlistItem,
  type WatchlistCriteriaType,
  type WatchlistFormErrors,
} from "@/lib/watchlist";

const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60";

const FIELD_CLASS =
  "w-full bg-surface-elevated border border-border rounded-md px-3 py-2 text-sm text-text-primary " +
  "placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-accent/60 focus:border-accent/50";

export function WatchlistEntryCard({ item }: { item: WatchlistItem }) {
  const [editing, setEditing] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  const [criteriaType, setCriteriaType] = useState<WatchlistCriteriaType>(item.criteria_type);
  const [threshold, setThreshold] = useState(String(item.threshold));
  const [notes, setNotes] = useState(item.notes ?? "");
  const [errors, setErrors] = useState<WatchlistFormErrors>({});

  const updateMutation = useUpdateWatchlistItem();
  const deleteMutation = useDeleteWatchlistItem();

  const status = criteriaStatus(item);

  // Focus management: moving the action under the pointer/keyboard user is a
  // swap, so move focus with it (confirm button on delete, first field on edit).
  const confirmDeleteRef = useRef<HTMLButtonElement>(null);
  const firstEditFieldRef = useRef<HTMLSelectElement>(null);

  useEffect(() => {
    if (confirmingDelete) confirmDeleteRef.current?.focus();
  }, [confirmingDelete]);

  useEffect(() => {
    if (editing) firstEditFieldRef.current?.focus();
  }, [editing]);

  const startEdit = () => {
    setCriteriaType(item.criteria_type);
    setThreshold(String(item.threshold));
    setNotes(item.notes ?? "");
    setErrors({});
    updateMutation.reset();
    setEditing(true);
  };

  const handleSave = (e: React.FormEvent) => {
    e.preventDefault();
    const validation = validateWatchlistInput({
      ticker: item.ticker, // unchanged; validated for completeness
      threshold,
      notes,
    });
    setErrors(validation);
    if (Object.keys(validation).length > 0) return;

    updateMutation.mutate(
      {
        id: item.id,
        patch: {
          criteria_type: criteriaType,
          threshold: Number(threshold),
          notes: notes.trim() ? notes.trim() : null,
        },
      },
      { onSuccess: () => setEditing(false) }
    );
  };

  const thresholdErrId = `wl-edit-threshold-err-${item.id}`;
  const notesErrId = `wl-edit-notes-err-${item.id}`;

  return (
    <li className="data-card p-4 space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="ticker-symbol text-sm">{item.ticker}</span>
            {status === "met" ? (
              <span className="badge-accent">Criteria met</span>
            ) : status === "not_met" ? (
              <span className="badge-surface">Not met</span>
            ) : (
              <span className="badge-surface italic normal-case tracking-normal">
                Unknown — no trusted current price
              </span>
            )}
          </div>
          {!editing && (
            <p className="text-sm text-text-secondary mt-1">
              {formatCriteriaSentence(item.criteria_type, item.threshold)}
            </p>
          )}
        </div>

        {!editing && (
          <div className="flex items-center gap-1 shrink-0">
            <button
              type="button"
              onClick={startEdit}
              className={cn("btn-ghost min-h-[40px]", FOCUS_RING)}
              aria-label={`Edit watchlist entry for ${item.ticker}`}
            >
              Edit
            </button>
            {confirmingDelete ? (
              <span className="inline-flex items-center gap-1">
                <button
                  ref={confirmDeleteRef}
                  type="button"
                  onClick={() => deleteMutation.mutate(item.id)}
                  disabled={deleteMutation.isPending}
                  className={cn("btn-danger min-h-[40px]", FOCUS_RING)}
                  aria-label={`Confirm delete of ${item.ticker} watchlist entry`}
                >
                  {deleteMutation.isPending ? "Deleting…" : "Confirm"}
                </button>
                <button
                  type="button"
                  onClick={() => setConfirmingDelete(false)}
                  className={cn("btn-ghost min-h-[40px]", FOCUS_RING)}
                >
                  Cancel
                </button>
              </span>
            ) : (
              <button
                type="button"
                onClick={() => {
                  deleteMutation.reset();
                  setConfirmingDelete(true);
                }}
                className={cn("btn-ghost min-h-[40px]", FOCUS_RING)}
                aria-label={`Delete watchlist entry for ${item.ticker}`}
              >
                Delete
              </button>
            )}
          </div>
        )}
      </div>

      {/* Price context */}
      {!editing && (
        <p className="text-xs text-text-muted">
          {item.current_price !== null ? (
            <>
              Current price{" "}
              <span className="data-value-xs text-text-primary">
                {formatCurrency(item.current_price)}
              </span>
              {item.price_as_of && <> · as of {relativeAgeLabel(item.price_as_of)}</>}
            </>
          ) : (
            "No trusted current price available right now."
          )}
        </p>
      )}

      {!editing && item.notes && (
        <p className="text-xs text-text-secondary leading-relaxed border-t border-border-subtle/60 pt-2">
          {item.notes}
        </p>
      )}

      {/* Edit-in-place form */}
      {editing && (
        <form onSubmit={handleSave} className="space-y-3" noValidate>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label
                htmlFor={`wl-edit-criteria-${item.id}`}
                className="metric-label block mb-1"
              >
                Criterion
              </label>
              <select
                ref={firstEditFieldRef}
                id={`wl-edit-criteria-${item.id}`}
                value={criteriaType}
                onChange={e => setCriteriaType(e.target.value as WatchlistCriteriaType)}
                className={FIELD_CLASS}
              >
                <option value="price_below">{criteriaTypeLabel("price_below")}</option>
                <option value="price_above">{criteriaTypeLabel("price_above")}</option>
              </select>
            </div>
            <div>
              <label
                htmlFor={`wl-edit-threshold-${item.id}`}
                className="metric-label block mb-1"
              >
                Threshold (USD)
              </label>
              <input
                id={`wl-edit-threshold-${item.id}`}
                type="number"
                inputMode="decimal"
                step="any"
                min="0"
                value={threshold}
                onChange={e => setThreshold(e.target.value)}
                aria-invalid={!!errors.threshold}
                aria-describedby={errors.threshold ? thresholdErrId : undefined}
                className={FIELD_CLASS}
              />
              {errors.threshold && (
                <p id={thresholdErrId} className="text-xs text-negative mt-1">
                  {errors.threshold}
                </p>
              )}
            </div>
          </div>
          <div>
            <label htmlFor={`wl-edit-notes-${item.id}`} className="metric-label block mb-1">
              Note (optional)
            </label>
            <input
              id={`wl-edit-notes-${item.id}`}
              type="text"
              value={notes}
              onChange={e => setNotes(e.target.value)}
              maxLength={500}
              aria-invalid={!!errors.notes}
              aria-describedby={errors.notes ? notesErrId : undefined}
              className={FIELD_CLASS}
            />
            {errors.notes && (
              <p id={notesErrId} className="text-xs text-negative mt-1">
                {errors.notes}
              </p>
            )}
          </div>

          <div className="flex items-center gap-2" aria-live="polite">
            <button
              type="submit"
              disabled={updateMutation.isPending}
              className={cn("btn-primary min-h-[40px]", FOCUS_RING)}
            >
              {updateMutation.isPending ? "Saving…" : "Save"}
            </button>
            <button
              type="button"
              onClick={() => {
                updateMutation.reset();
                setEditing(false);
              }}
              className={cn("btn-ghost min-h-[40px]", FOCUS_RING)}
            >
              Cancel
            </button>
            {updateMutation.isError && (
              <p className="text-xs text-negative">
                {updateMutation.error instanceof Error
                  ? updateMutation.error.message
                  : "Failed to save changes."}
              </p>
            )}
          </div>
        </form>
      )}

      {/* Delete error surfaced inline */}
      {deleteMutation.isError && !editing && (
        <p className="text-xs text-negative" aria-live="polite">
          {deleteMutation.error instanceof Error
            ? deleteMutation.error.message
            : "Failed to delete entry."}
        </p>
      )}
    </li>
  );
}
