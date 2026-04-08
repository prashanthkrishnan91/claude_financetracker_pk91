"use client";

import { PortfolioSummaryCard } from "@/components/holdings/PortfolioSummaryCard";
import { HoldingsList } from "@/components/holdings/HoldingsList";
import { PortfolioChart } from "@/components/charts/PortfolioChart";
import { BottomNav } from "@/components/navigation/BottomNav";

export default function DashboardPage() {
  return (
    <div className="min-h-screen pb-20 lg:pb-0">
      {/* Top bar */}
      <header className="sticky top-0 z-50 bg-background/80 backdrop-blur-xl border-b border-border px-4 py-3">
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <h1 className="text-xl font-display text-text-primary">Portfolio</h1>
          <div className="flex items-center gap-3">
            <span className="text-xs text-text-muted">v2.0</span>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 py-6 space-y-6">
        {/* Portfolio Summary */}
        <PortfolioSummaryCard />

        {/* Chart */}
        <section className="card-glass p-4">
          <PortfolioChart />
        </section>

        {/* Holdings */}
        <section>
          <h2 className="text-lg font-semibold text-text-primary mb-3">
            Holdings
          </h2>
          <HoldingsList />
        </section>
      </main>

      {/* Mobile bottom nav */}
      <BottomNav />
    </div>
  );
}
