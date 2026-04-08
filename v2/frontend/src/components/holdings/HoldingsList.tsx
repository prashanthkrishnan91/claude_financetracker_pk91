"use client";

import { cn, formatCurrency, formatPercent, pnlClass } from "@/lib/utils";

// Phase 3: Replace with real data from useQuery + api.positions.list()
const MOCK_HOLDINGS = [
  { ticker: "NVDA", name: "NVIDIA", shares: 35.50, avgCost: 116.02, price: 875.22, category: "Core" },
  { ticker: "NFLX", name: "Netflix", shares: 21.33, avgCost: 101.32, price: 1050.00, category: "Core" },
  { ticker: "VOO", name: "Vanguard S&P 500", shares: 7.60, avgCost: 570.62, price: 545.00, category: "ETF" },
  { ticker: "AAPL", name: "Apple", shares: 16.11, avgCost: 213.03, price: 228.50, category: "Core" },
  { ticker: "VYM", name: "Vanguard Hi-Div", shares: 21.91, avgCost: 136.97, price: 132.40, category: "ETF" },
  { ticker: "BTC", name: "Bitcoin", shares: 0.034, avgCost: 66997, price: 83000, category: "Crypto" },
];

export function HoldingsList() {
  return (
    <div className="space-y-2">
      {MOCK_HOLDINGS.map((h) => {
        const marketValue = h.shares * h.price;
        const costBasis = h.shares * h.avgCost;
        const pnl = marketValue - costBasis;
        const pnlPct = costBasis > 0 ? (pnl / costBasis) * 100 : 0;

        return (
          <div
            key={h.ticker}
            className="card-glass px-4 py-3 flex items-center justify-between hover:bg-surface-elevated/50 transition-colors cursor-pointer"
          >
            {/* Left: Ticker + Name */}
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-mono font-semibold text-text-primary">
                  {h.ticker}
                </span>
                <span className="text-xs px-1.5 py-0.5 rounded bg-surface-elevated text-text-muted">
                  {h.category}
                </span>
              </div>
              <p className="text-xs text-text-secondary truncate">{h.name}</p>
            </div>

            {/* Right: Price + P&L */}
            <div className="text-right ml-4">
              <p className="font-mono text-sm text-text-primary">
                {formatCurrency(marketValue)}
              </p>
              <p className={cn("text-xs font-mono", pnlClass(pnl))}>
                {formatCurrency(pnl)} ({formatPercent(pnlPct)})
              </p>
            </div>
          </div>
        );
      })}
    </div>
  );
}
