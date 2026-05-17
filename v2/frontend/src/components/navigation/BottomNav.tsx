"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { href: "/dashboard", label: "Today", icon: BarChartIcon },
  { href: "/dashboard/portfolio", label: "Portfolio", icon: BriefcaseIcon },
  { href: "/dashboard/recommendations", label: "Intel", icon: LightbulbIcon },
  { href: "/dashboard/deposits", label: "Deploy", icon: WalletIcon },
  { href: "/dashboard/alerts", label: "Alerts", icon: BellIcon },
  { href: "/dashboard/drip", label: "DRIP", icon: DropletIcon },
  { href: "/dashboard/import", label: "Import", icon: UploadIcon },
  { href: "/settings", label: "Settings", icon: GearIcon },
];

export function BottomNav() {
  const pathname = usePathname();

  return (
    <nav
      className="fixed bottom-0 left-0 right-0 z-50 border-t border-border-subtle lg:hidden"
      style={{
        background: "var(--bottom-nav-glass-bg)",
        backdropFilter: "blur(20px)",
        WebkitBackdropFilter: "blur(20px)",
      }}
    >
      <div className="flex justify-around items-center h-16 max-w-lg mx-auto">
        {NAV_ITEMS.map((item) => {
          const active =
            pathname === item.href ||
            (item.href !== "/dashboard" && pathname.startsWith(item.href));
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "relative flex flex-col items-center gap-0.5 px-2 py-2 rounded-md transition-colors duration-160",
                active
                  ? "text-accent"
                  : "text-text-muted hover:text-text-secondary"
              )}
            >
              {/* Engraved top indicator — marks active item from above */}
              {active && (
                <span className="absolute top-0 left-1/2 -translate-x-1/2 w-6 h-[2px] bg-accent rounded-full opacity-80" />
              )}
              <item.icon
                className={cn("w-5 h-5", active ? "opacity-100" : "opacity-60")}
                strokeWidth={active ? 2 : 1.5}
              />
              <span
                className={cn(
                  "text-[9px] font-semibold tracking-label uppercase",
                  active ? "text-accent" : "text-text-muted"
                )}
              >
                {item.label}
              </span>
            </Link>
          );
        })}
      </div>
      {/* Safe area spacer for iOS home indicator */}
      <div style={{ height: "env(safe-area-inset-bottom)" }} />
    </nav>
  );
}

export function SideNav() {
  const pathname = usePathname();

  return (
    <nav className="hidden lg:flex flex-col w-56 border-r border-border-subtle bg-surface p-4 gap-1 min-h-screen sticky top-0 h-screen overflow-y-auto">

      {/* Brand mark — editorial serif for the product name */}
      <Link href="/dashboard" className="block mb-8 group">
        <span className="block font-display text-base font-normal text-text-primary leading-none">
          Portfolio
        </span>
        <span className="block text-[10px] uppercase tracking-widest2 text-text-muted mt-0.5 opacity-50 group-hover:opacity-70 transition-opacity duration-160">
          Intelligence
        </span>
      </Link>

      {/* Navigation items */}
      <div className="space-y-px">
        {NAV_ITEMS.map((item) => {
          const active =
            pathname === item.href ||
            (item.href !== "/dashboard" && pathname.startsWith(item.href));
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex items-center gap-3 py-2 text-sm transition-colors duration-160",
                // Engraved active rule: 2pt left border + very subtle accent bg
                active
                  ? "text-accent font-medium bg-accent/[0.06] pl-2.5 pr-3 border-l-2 border-accent"
                  : "text-text-secondary hover:text-text-primary hover:bg-surface-elevated/60 px-3 border-l-2 border-transparent"
              )}
            >
              <item.icon
                className="w-4 h-4 shrink-0"
                strokeWidth={active ? 2 : 1.5}
              />
              <span className="text-sm">{item.label}</span>
            </Link>
          );
        })}
      </div>

      {/* Footer chrome */}
      <div className="mt-auto pt-3 border-t border-border-subtle/40">
        {/* Data-health dot — placeholder state (wired in Stage 4D) */}
        <div className="flex items-center gap-2 px-1 py-1.5 text-text-muted opacity-40">
          <span className="w-1.5 h-1.5 rounded-full bg-border-strong shrink-0" />
          <span className="text-[10px] uppercase tracking-label">Data health</span>
        </div>
        {/* Version / build stamp */}
        <p className="text-[10px] font-mono text-text-muted opacity-25 tracking-widest px-1 mt-1">
          v2
        </p>
      </div>
    </nav>
  );
}

// ─── Icons (1.5 px stroke, geometric) ─────────────────────────────────────────

function BarChartIcon({
  className,
  strokeWidth = 1.5,
}: {
  className?: string;
  strokeWidth?: number;
}) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={strokeWidth}>
      <path d="M18 20V10M12 20V4M6 20v-6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function BriefcaseIcon({
  className,
  strokeWidth = 1.5,
}: {
  className?: string;
  strokeWidth?: number;
}) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={strokeWidth}>
      <rect x="2" y="7" width="20" height="14" rx="2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M16 7V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M12 12v3" strokeLinecap="round" />
    </svg>
  );
}

function LightbulbIcon({
  className,
  strokeWidth = 1.5,
}: {
  className?: string;
  strokeWidth?: number;
}) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={strokeWidth}>
      <path d="M9 18h6M10 22h4M12 2a7 7 0 0 0-4 12.7V17h8v-2.3A7 7 0 0 0 12 2z" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function WalletIcon({
  className,
  strokeWidth = 1.5,
}: {
  className?: string;
  strokeWidth?: number;
}) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={strokeWidth}>
      <path d="M21 12V7H5a2 2 0 0 1 0-4h14v4" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M3 5v14a2 2 0 0 0 2 2h16v-5" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M18 12a2 2 0 0 0 0 4h4v-4h-4z" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function DropletIcon({
  className,
  strokeWidth = 1.5,
}: {
  className?: string;
  strokeWidth?: number;
}) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={strokeWidth}>
      <path d="M12 2.69l5.66 5.66a8 8 0 1 1-11.31 0z" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function UploadIcon({
  className,
  strokeWidth = 1.5,
}: {
  className?: string;
  strokeWidth?: number;
}) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={strokeWidth}>
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function GearIcon({
  className,
  strokeWidth = 1.5,
}: {
  className?: string;
  strokeWidth?: number;
}) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={strokeWidth}>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  );
}

function BellIcon({
  className,
  strokeWidth = 1.5,
}: {
  className?: string;
  strokeWidth?: number;
}) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={strokeWidth}>
      <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M13.73 21a2 2 0 0 1-3.46 0" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
