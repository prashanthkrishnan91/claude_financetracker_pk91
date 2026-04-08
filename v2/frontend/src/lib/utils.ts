import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Format a number as USD currency */
export function formatCurrency(value: number, compact = false): string {
  if (compact && Math.abs(value) >= 1000) {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      notation: "compact",
      maximumFractionDigits: 1,
    }).format(value);
  }
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

/** Format a percentage with sign */
export function formatPercent(value: number): string {
  const sign = value >= 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

/** Format a number with commas */
export function formatNumber(value: number, decimals = 2): string {
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(value);
}

/** Get the P&L CSS class */
export function pnlClass(value: number): string {
  if (value > 0) return "pnl-positive";
  if (value < 0) return "pnl-negative";
  return "text-text-muted";
}
