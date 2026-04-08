/**
 * Shared TypeScript types for the Portfolio Intelligence Platform v2.
 * These mirror the backend Pydantic models.
 */

export type ActionType = "BUY" | "SELL" | "TRIM" | "HOLD" | "REVIEW";
export type CategoryType = "Crypto" | "Core" | "ETF" | "Other" | "IPO" | "SELL";

export interface User {
  id: string;
  email: string;
  display_name?: string;
  deposit_amount: number;
  deposit_frequency: "weekly" | "biweekly" | "monthly";
  theme: "dark" | "light";
  has_plaid: boolean;
  has_finnhub: boolean;
  has_alpaca: boolean;
}
