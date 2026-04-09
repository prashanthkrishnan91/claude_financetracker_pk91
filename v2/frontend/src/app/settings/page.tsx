"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import {
  useSyncPlaid,
  usePlaidStatus,
  useRefreshPrices,
  useUserProfile,
  useUpdateProfile,
  useUpdateApiKeys,
  useCashBalance,
  useSetCash,
} from "@/lib/hooks";
import { Spinner } from "@/components/ui/Spinner";
import { cn, formatCurrency } from "@/lib/utils";
import Link from "next/link";
import type { ApiKeysUpdate } from "@/lib/api";

export default function SettingsPage() {
  const { user, loading, signOut } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user) {
      router.push("/login");
    }
  }, [user, loading, router]);

  if (loading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="inline-block h-8 w-8 animate-spin rounded-full border-2 border-current border-t-transparent text-accent" />
      </div>
    );
  }
  if (!user) return null;

  return (
    <div className="min-h-screen pb-20 lg:pb-0">
      <header className="sticky top-0 z-50 bg-background/80 backdrop-blur-xl border-b border-border px-4 py-3">
        <div className="max-w-3xl mx-auto flex items-center justify-between">
          <h1 className="text-xl font-display text-text-primary">Settings</h1>
          <div className="flex items-center gap-3">
            <Link
              href="/dashboard"
              className="text-xs text-text-muted hover:text-text-primary transition-colors"
            >
              Dashboard
            </Link>
            <button
              onClick={signOut}
              className="text-xs px-3 py-1.5 rounded-md border border-danger/30 text-danger hover:bg-danger/10 transition-colors"
            >
              Sign out
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-4 py-6 space-y-6">
        <ProfileSection />
        <CashOverrideSection />
        <ApiKeysSection />
        <DataSection />
        <AboutSection />
      </main>
    </div>
  );
}

// ── Profile Section ────────────────────────────────────────────────────────────

function ProfileSection() {
  const { data: profile, isLoading } = useUserProfile();
  const updateProfile = useUpdateProfile();
  const [displayName, setDisplayName] = useState("");
  const [depositAmount, setDepositAmount] = useState("");
  const [depositFrequency, setDepositFrequency] = useState("biweekly");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (profile) {
      setDisplayName(profile.display_name ?? "");
      setDepositAmount(profile.deposit_amount?.toString() ?? "");
      setDepositFrequency(profile.deposit_frequency ?? "biweekly");
    }
  }, [profile]);

  function handleSave() {
    updateProfile.mutate(
      {
        display_name: displayName || null,
        deposit_amount: parseFloat(depositAmount) || 0,
        deposit_frequency: depositFrequency,
      },
      {
        onSuccess: () => {
          setSaved(true);
          setTimeout(() => setSaved(false), 2000);
        },
      }
    );
  }

  return (
    <Section title="Profile">
      {isLoading ? (
        <div className="flex items-center gap-2 text-text-muted text-xs py-2">
          <Spinner className="h-3 w-3" /> Loading profile...
        </div>
      ) : (
        <div className="space-y-4">
          <div className="space-y-1.5">
            <label className="text-xs text-text-muted">Display Name</label>
            <input
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="Your name"
              className="w-full px-3 py-2 bg-surface border border-border rounded-lg text-text-primary text-sm focus:outline-none focus:ring-1 focus:ring-accent"
            />
          </div>

          <div className="space-y-1.5">
            <label className="text-xs text-text-muted">Deposit Amount</label>
            <div className="relative">
              <span className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted text-sm">$</span>
              <input
                type="number"
                value={depositAmount}
                onChange={(e) => setDepositAmount(e.target.value)}
                className="w-full pl-7 pr-3 py-2 bg-surface border border-border rounded-lg text-text-primary font-mono text-sm focus:outline-none focus:ring-1 focus:ring-accent"
              />
            </div>
          </div>

          <div className="space-y-1.5">
            <label className="text-xs text-text-muted">Deposit Frequency</label>
            <select
              value={depositFrequency}
              onChange={(e) => setDepositFrequency(e.target.value)}
              className="w-full px-3 py-2 bg-surface border border-border rounded-lg text-text-primary text-sm focus:outline-none focus:ring-1 focus:ring-accent"
            >
              <option value="weekly">Weekly</option>
              <option value="biweekly">Biweekly</option>
              <option value="monthly">Monthly</option>
            </select>
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={handleSave}
              disabled={updateProfile.isPending}
              className="px-4 py-2 bg-accent text-background font-semibold rounded-lg text-sm hover:bg-accent-hover transition-colors disabled:opacity-50"
            >
              {updateProfile.isPending ? (
                <span className="flex items-center gap-2"><Spinner className="h-3 w-3" /> Saving...</span>
              ) : (
                "Save Profile"
              )}
            </button>
            {saved && (
              <span className="text-xs text-accent">Saved!</span>
            )}
            {updateProfile.isError && (
              <span className="text-xs text-danger">Save failed.</span>
            )}
          </div>
        </div>
      )}
    </Section>
  );
}

// ── Cash Override Section ──────────────────────────────────────────────────────

function CashOverrideSection() {
  const { data: cash, isLoading } = useCashBalance();
  const setCash = useSetCash();
  const [editing, setEditing] = useState(false);
  const [inputVal, setInputVal] = useState("");

  function startEdit() {
    setInputVal(cash?.manual_override?.toString() ?? cash?.cash_balance?.toString() ?? "0");
    setEditing(true);
  }

  function handleSave() {
    const parsed = parseFloat(inputVal);
    if (!isNaN(parsed)) {
      setCash.mutate(parsed, {
        onSuccess: () => setEditing(false),
      });
    }
  }

  function handleClear() {
    setCash.mutate(null, {
      onSuccess: () => setEditing(false),
    });
  }

  const sourceStyle =
    cash?.source === "plaid"
      ? "bg-blue-500/10 text-blue-400 border-blue-500/20"
      : cash?.source === "manual"
      ? "bg-yellow-500/10 text-yellow-400 border-yellow-500/20"
      : "bg-surface-elevated text-text-muted border-border";

  return (
    <Section title="Cash Override">
      {isLoading ? (
        <div className="flex items-center gap-2 text-text-muted text-xs py-2">
          <Spinner className="h-3 w-3" /> Loading...
        </div>
      ) : (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <span className="font-mono text-text-primary font-semibold">
                {cash ? formatCurrency(cash.cash_balance) : "—"}
              </span>
              {cash?.source && (
                <span className={cn("text-[10px] px-2 py-0.5 rounded-full border font-semibold uppercase", sourceStyle)}>
                  {cash.source}
                </span>
              )}
            </div>
            {!editing && (
              <button
                onClick={startEdit}
                className="text-xs text-text-muted hover:text-text-primary transition-colors flex items-center gap-1.5 px-2 py-1 rounded hover:bg-surface-elevated"
              >
                <PencilIcon className="w-3 h-3" />
                Edit
              </button>
            )}
          </div>

          {editing && (
            <div className="space-y-2">
              <div className="flex gap-2 items-center">
                <div className="relative flex-1">
                  <span className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted text-sm">$</span>
                  <input
                    type="number"
                    value={inputVal}
                    onChange={(e) => setInputVal(e.target.value)}
                    className="w-full pl-7 pr-3 py-2 bg-surface border border-border rounded-lg text-text-primary font-mono text-sm focus:outline-none focus:ring-1 focus:ring-accent"
                    autoFocus
                  />
                </div>
                <button
                  onClick={handleSave}
                  disabled={setCash.isPending}
                  className="px-3 py-2 bg-accent text-background rounded-lg text-xs font-semibold hover:bg-accent-hover disabled:opacity-50 transition-colors"
                >
                  {setCash.isPending ? <Spinner className="h-3 w-3" /> : "Save"}
                </button>
                <button
                  onClick={() => setEditing(false)}
                  className="px-3 py-2 bg-surface-elevated text-text-muted rounded-lg text-xs hover:text-text-primary transition-colors"
                >
                  Cancel
                </button>
              </div>
              {cash?.manual_override !== null && cash?.manual_override !== undefined && (
                <button
                  onClick={handleClear}
                  className="text-xs text-danger hover:text-danger/80 transition-colors"
                >
                  Clear manual override
                </button>
              )}
            </div>
          )}

          <p className="text-xs text-text-muted">
            Override the cash balance from Plaid with a manual value. Set to null to revert to Plaid data.
          </p>
        </div>
      )}
    </Section>
  );
}

// ── API Keys Section ───────────────────────────────────────────────────────────

function ApiKeysSection() {
  const { data: profile } = useUserProfile();
  const updateApiKeys = useUpdateApiKeys();
  const [keys, setKeys] = useState<ApiKeysUpdate>({});
  const [saved, setSaved] = useState(false);

  function setKey(field: keyof ApiKeysUpdate, value: string) {
    setKeys((prev) => ({ ...prev, [field]: value }));
  }

  function handleSave() {
    // Only submit non-empty keys
    const filtered: ApiKeysUpdate = {};
    for (const [k, v] of Object.entries(keys)) {
      if (v && v.trim()) {
        (filtered as Record<string, string>)[k] = v.trim();
      }
    }
    updateApiKeys.mutate(filtered, {
      onSuccess: () => {
        setSaved(true);
        setKeys({});
        setTimeout(() => setSaved(false), 2500);
      },
    });
  }

  const keyFields: Array<{
    field: keyof ApiKeysUpdate;
    label: string;
    hasFlag?: boolean;
    flagKey?: keyof any;
  }> = [
    { field: "plaid_client_id", label: "Plaid Client ID", hasFlag: true, flagKey: "has_plaid" },
    { field: "plaid_secret", label: "Plaid Secret", hasFlag: true, flagKey: "has_plaid" },
    { field: "finnhub_api_key", label: "Finnhub API Key", hasFlag: true, flagKey: "has_finnhub" },
    { field: "polygon_api_key", label: "Polygon API Key", hasFlag: true, flagKey: "has_polygon" },
    { field: "alpaca_api_key", label: "Alpaca API Key", hasFlag: true, flagKey: "has_alpaca" },
    { field: "alpaca_secret_key", label: "Alpaca Secret Key", hasFlag: true, flagKey: "has_alpaca" },
    { field: "anthropic_api_key", label: "Anthropic API Key" },
  ];

  return (
    <Section title="API Keys">
      <div className="space-y-4">
        <p className="text-xs text-text-muted">
          Enter new values to update keys. Leave blank to keep existing values. Keys are stored securely on the server.
        </p>

        <div className="space-y-3">
          {keyFields.map(({ field, label, hasFlag, flagKey }) => {
            const isConfigured = hasFlag && flagKey && (profile as any)?.[flagKey] === true;
            return (
              <div key={field} className="space-y-1.5">
                <div className="flex items-center gap-2">
                  <label className="text-xs text-text-muted">{label}</label>
                  {isConfigured && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent/10 text-accent border border-accent/20 font-semibold">
                      Configured
                    </span>
                  )}
                </div>
                <input
                  type="password"
                  value={keys[field] ?? ""}
                  onChange={(e) => setKey(field, e.target.value)}
                  placeholder={isConfigured ? "••••••••••••" : "Enter key..."}
                  className="w-full px-3 py-2 bg-surface border border-border rounded-lg text-text-primary text-sm font-mono focus:outline-none focus:ring-1 focus:ring-accent placeholder:text-text-muted"
                />
              </div>
            );
          })}
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={handleSave}
            disabled={updateApiKeys.isPending}
            className="px-4 py-2 bg-accent text-background font-semibold rounded-lg text-sm hover:bg-accent-hover transition-colors disabled:opacity-50"
          >
            {updateApiKeys.isPending ? (
              <span className="flex items-center gap-2"><Spinner className="h-3 w-3" /> Saving...</span>
            ) : (
              "Save API Keys"
            )}
          </button>
          {saved && (
            <span className="text-xs text-accent">Keys updated!</span>
          )}
          {updateApiKeys.isError && (
            <span className="text-xs text-danger">Save failed.</span>
          )}
        </div>
      </div>
    </Section>
  );
}

// ── Data Section ───────────────────────────────────────────────────────────────

function DataSection() {
  const plaidSync = useSyncPlaid();
  const { data: plaidStatus } = usePlaidStatus();
  const refreshPrices = useRefreshPrices();
  const [forceSync, setForceSync] = useState(false);

  return (
    <Section title="Data">
      <div className="space-y-5">
        {/* Plaid Sync */}
        <div className="space-y-3">
          <p className="text-xs font-semibold text-text-secondary">Plaid / Robinhood Sync</p>
          {plaidStatus && (
            <div className="flex items-center gap-2.5">
              <StatusDot
                status={
                  plaidStatus.status === "fresh"
                    ? "green"
                    : plaidStatus.status === "stale"
                    ? "yellow"
                    : "gray"
                }
              />
              <p className="text-xs text-text-secondary">
                {plaidStatus.status === "fresh"
                  ? `Synced ${plaidStatus.age_hours?.toFixed(1)}h ago`
                  : plaidStatus.status === "stale"
                  ? `Stale (${plaidStatus.age_hours?.toFixed(1)}h old)`
                  : "Never synced"}
              </p>
              {plaidStatus.holdings_count !== undefined && plaidStatus.holdings_count > 0 && (
                <span className="text-xs text-text-muted">
                  {plaidStatus.holdings_count} holdings
                </span>
              )}
            </div>
          )}

          <label className="flex items-center gap-2 text-xs text-text-muted cursor-pointer">
            <input
              type="checkbox"
              checked={forceSync}
              onChange={(e) => setForceSync(e.target.checked)}
              className="rounded bg-surface border-border"
            />
            Force re-sync (bypass 24h cache)
          </label>

          <button
            onClick={() => plaidSync.mutate(forceSync)}
            disabled={plaidSync.isPending}
            className="px-4 py-2 bg-accent text-background font-semibold rounded-lg text-sm hover:bg-accent-hover transition-colors disabled:opacity-50"
          >
            {plaidSync.isPending ? (
              <span className="flex items-center gap-2"><Spinner className="h-3 w-3" /> Syncing...</span>
            ) : (
              "Sync with Plaid"
            )}
          </button>

          {plaidSync.isSuccess && (
            <p className="text-xs text-accent">{plaidSync.data.message}</p>
          )}
          {plaidSync.isError && (
            <p className="text-xs text-danger">Sync failed: {plaidSync.error?.message}</p>
          )}
        </div>

        <hr className="border-border/50" />

        {/* Price Refresh */}
        <div className="space-y-3">
          <p className="text-xs font-semibold text-text-secondary">Price Data</p>
          <p className="text-xs text-text-muted">
            Fires all price sources concurrently (yfinance, Finnhub, Alpaca, CoinGecko).
          </p>
          <button
            onClick={() => refreshPrices.mutate()}
            disabled={refreshPrices.isPending}
            className="px-4 py-2 bg-surface-elevated text-text-primary font-medium rounded-lg text-sm hover:bg-border transition-colors disabled:opacity-50 border border-border"
          >
            {refreshPrices.isPending ? (
              <span className="flex items-center gap-2"><Spinner className="h-3 w-3" /> Refreshing...</span>
            ) : (
              "Refresh All Prices"
            )}
          </button>
          {refreshPrices.isSuccess && refreshPrices.data && (
            <div className="text-xs text-text-muted space-y-1">
              <p>Fresh: {refreshPrices.data.fresh} / {refreshPrices.data.total}</p>
              {refreshPrices.data.sources_used.length > 0 && (
                <p>Sources: {refreshPrices.data.sources_used.join(", ")}</p>
              )}
            </div>
          )}
        </div>

        <hr className="border-border/50" />

        {/* CSV Import */}
        <div className="space-y-2">
          <p className="text-xs font-semibold text-text-secondary">Data Import</p>
          <Link
            href="/dashboard/import"
            className="inline-block px-4 py-2 bg-surface-elevated text-text-primary font-medium rounded-lg text-sm hover:bg-border transition-colors border border-border"
          >
            Import Robinhood CSV
          </Link>
          <p className="text-xs text-text-muted">
            Upload Robinhood transaction exports. SHA-256 fingerprinting prevents duplicate imports.
          </p>
        </div>
      </div>
    </Section>
  );
}

// ── About Section ──────────────────────────────────────────────────────────────

function AboutSection() {
  return (
    <Section title="About">
      <div className="text-xs text-text-muted space-y-1">
        <p>Portfolio Intelligence Platform v2.0</p>
        <p>FastAPI + Next.js 14 + Supabase + Tailwind CSS</p>
        <p>Concurrent multi-source price engine</p>
      </div>
    </Section>
  );
}

// ── Shared Components ──────────────────────────────────────────────────────────

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="card-glass p-4 space-y-3">
      <h2 className="text-sm font-semibold text-text-secondary uppercase tracking-wide">
        {title}
      </h2>
      {children}
    </div>
  );
}

function StatusDot({ status }: { status: "green" | "yellow" | "gray" }) {
  return (
    <span
      className={cn(
        "w-2 h-2 rounded-full shrink-0",
        status === "green"
          ? "bg-accent"
          : status === "yellow"
          ? "bg-warning"
          : "bg-text-muted"
      )}
    />
  );
}

function PencilIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
