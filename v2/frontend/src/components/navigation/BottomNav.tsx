"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

// The product has exactly three views. Both navs render the same three items.
const NAV_ITEMS = [
  { href: "/dashboard/portfolio", label: "Positions", icon: BriefcaseIcon },
  { href: "/dashboard/recommendations", label: "Recommendations", icon: LightbulbIcon },
  { href: "/dashboard/watchlist", label: "Watchlist", icon: EyeIcon },
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
          const active = pathname === item.href || pathname.startsWith(item.href);
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
      <Link href="/dashboard/portfolio" className="block mb-8 group">
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
          const active = pathname === item.href || pathname.startsWith(item.href);
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
        {/* Version / build stamp */}
        <p className="text-[10px] font-mono text-text-muted opacity-25 tracking-widest px-1 mt-1">
          v2
        </p>
      </div>
    </nav>
  );
}

// ─── Icons (1.5 px stroke, geometric) ─────────────────────────────────────────

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

function EyeIcon({
  className,
  strokeWidth = 1.5,
}: {
  className?: string;
  strokeWidth?: number;
}) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={strokeWidth}>
      <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="12" cy="12" r="3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
