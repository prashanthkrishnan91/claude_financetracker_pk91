"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";

// Phase 3: Replace with real data from useQuery + api.prices.history()
// and integrate recharts for the actual line chart
const PERIODS = ["1W", "1M", "3M", "6M", "1Y"] as const;
type Period = (typeof PERIODS)[number];

export function PortfolioChart() {
  const [period, setPeriod] = useState<Period>("1Y");

  return (
    <div className="space-y-4">
      {/* Period selector */}
      <div className="flex gap-1">
        {PERIODS.map((p) => (
          <button
            key={p}
            onClick={() => setPeriod(p)}
            className={cn(
              "px-3 py-1 text-xs rounded-md transition-colors",
              period === p
                ? "bg-accent text-background font-semibold"
                : "text-text-muted hover:text-text-primary hover:bg-surface-elevated"
            )}
          >
            {p}
          </button>
        ))}
      </div>

      {/* Chart placeholder */}
      <div className="h-[200px] lg:h-[300px] flex items-center justify-center rounded-md bg-surface/50">
        <div className="text-center space-y-2">
          <p className="text-text-muted text-sm">
            Portfolio performance chart
          </p>
          <p className="text-text-muted text-xs">
            Phase 3: Recharts line chart with {period} data
          </p>
        </div>
      </div>
    </div>
  );
}
