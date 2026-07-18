"use client";

import { WatchlistPanel } from "@/components/watchlist/WatchlistPanel";

export default function WatchlistPage() {
  return (
    <>
      {/* Top bar */}
      <header className="sticky top-0 z-50 bg-background/80 backdrop-blur-xl border-b border-border px-4 py-3">
        <div className="max-w-3xl mx-auto">
          <h1 className="text-xl font-display text-text-primary">Watchlist</h1>
          <p className="text-[10px] uppercase tracking-label text-text-muted opacity-50 leading-none mt-0.5">
            Price criteria you set
          </p>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-4 py-6 space-y-4">
        {/* Honest one-liner: candidates only, never stock picks */}
        <p className="text-[11px] text-text-muted leading-snug">
          The app surfaces candidates when your price criteria are met — it never
          picks stocks for you.
        </p>

        <WatchlistPanel />
      </main>
    </>
  );
}
