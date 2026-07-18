"use client";

/**
 * DataHealthDrawer — Stage 4D calm data health surface.
 *
 * Accessible from Today (/dashboard) and Intel (/dashboard/recommendations).
 * Uses only existing frontend-readable data via React Query hooks.
 * Missing data shows "Not connected to this view yet" — never fake status.
 *
 * Accessibility: role=dialog, aria-modal, aria-labelledby, Escape close,
 * close button focused on open, backdrop click closes.
 */

import { useEffect, useRef } from "react";
import { cn } from "@/lib/utils";
import {
  useIntelV3Snapshot,
  usePortfolioSummary,
  usePlaidStatus,
} from "@/lib/hooks";
import { buildDataHealthRows } from "@/lib/intel-v3-evidence";
import { TrustStatusRow } from "./TrustPrimitives";

interface DataHealthDrawerProps {
  open: boolean;
  onClose: () => void;
}

function CloseIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5">
      <path d="M6.28 5.22a.75.75 0 0 0-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 1 0 1.06 1.06L10 11.06l3.72 3.72a.75.75 0 1 0 1.06-1.06L11.06 10l3.72-3.72a.75.75 0 0 0-1.06-1.06L10 8.94 6.28 5.22Z" />
    </svg>
  );
}

const FOCUSABLE_SELECTOR =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function DataHealthDrawer({ open, onClose }: DataHealthDrawerProps) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLElement>(null);

  const { data: intelSnapshot } = useIntelV3Snapshot();
  const { data: portfolioSummary } = usePortfolioSummary();
  const { data: plaidStatus } = usePlaidStatus();

  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, onClose]);

  useEffect(() => {
    if (open) closeRef.current?.focus();
  }, [open]);

  // Simple focus trap while the dialog is open: Tab cycles within the panel.
  // On close, focus returns to the element that opened the drawer.
  useEffect(() => {
    if (!open) return;
    const previouslyFocused =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const handler = (e: KeyboardEvent) => {
      if (e.key !== "Tab") return;
      const panel = panelRef.current;
      if (!panel) return;
      const focusables = Array.from(
        panel.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)
      );
      if (focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const active = document.activeElement;
      const inside = active instanceof HTMLElement && panel.contains(active);
      if (e.shiftKey) {
        if (!inside || active === first) {
          e.preventDefault();
          last.focus();
        }
      } else if (!inside || active === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handler);
    return () => {
      document.removeEventListener("keydown", handler);
      previouslyFocused?.focus();
    };
  }, [open]);

  if (!open) return null;

  const rows = buildDataHealthRows({
    intelSnapshotSource: intelSnapshot?.snapshot_source ?? null,
    intelFreshnessState: intelSnapshot?.evidence_freshness_state ?? null,
    pricesFresh: portfolioSummary?.prices_fresh ?? null,
    pricesStale: portfolioSummary?.prices_stale ?? null,
    plaidStatus: plaidStatus?.status ?? null,
    plaidLastSyncedAt: plaidStatus?.last_synced_at ?? null,
  });

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm motion-reduce:backdrop-blur-none"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Drawer panel — mobile: bottom sheet; desktop: right-side drawer */}
      <aside
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="data-health-title"
        className={cn(
          // Mobile: full-width bottom sheet
          "fixed inset-x-0 bottom-0 z-50",
          "max-h-[88dvh] rounded-t-2xl",
          "border-t border-border bg-background",
          "overflow-y-auto overscroll-contain",
          "sheet-slide-up",
          // Desktop: right-side drawer
          "lg:inset-x-auto lg:right-0 lg:top-0 lg:bottom-0",
          "lg:w-full lg:max-w-sm lg:max-h-none",
          "lg:rounded-none lg:border-t-0 lg:border-l",
          "lg:[animation:none]",
        )}
      >
        {/* Mobile drag handle */}
        <div className="lg:hidden flex justify-center pt-3 pb-1" aria-hidden="true">
          <div className="w-8 h-1 rounded-full bg-border-strong opacity-50" />
        </div>

        {/* ── Header ──────────────────────────────────────────────────────── */}
        <div className="sticky top-0 z-10 bg-background/95 backdrop-blur-sm border-b border-border px-5 py-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2
                id="data-health-title"
                className="text-sm font-bold text-text-primary"
              >
                Data Health
              </h2>
              <p className="text-[10px] text-text-muted mt-0.5">
                What&apos;s connected and current
              </p>
            </div>
            <button
              ref={closeRef}
              onClick={onClose}
              className={cn(
                "shrink-0 text-text-muted hover:text-text-primary transition-colors",
                "rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60",
                "motion-reduce:transition-none"
              )}
              aria-label="Close data health"
            >
              <CloseIcon />
            </button>
          </div>
        </div>

        {/* ── Body ────────────────────────────────────────────────────────── */}
        <div className="px-5 py-5 space-y-5">

          {/* Live health rows */}
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-widest text-text-muted mb-1">
              Current data connections
            </p>
            <div className="divide-y divide-border">
              {rows.map((row) => (
                <TrustStatusRow
                  key={row.label}
                  label={row.label}
                  status={row.status}
                  detail={row.detail}
                />
              ))}
            </div>
          </div>

        </div>
      </aside>
    </>
  );
}
