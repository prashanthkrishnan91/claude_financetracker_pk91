"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import {
  MOBILE_NAV_ITEMS,
  DESKTOP_NAV_ITEMS,
  SECONDARY_NAV_ITEMS,
  type NavItem,
} from "./nav-items";

// Icon lookup for the canonical nav constants (which stay pure-TS for tests).
const NAV_ICONS: Record<string, IconComponent> = {
  "/dashboard/positions": BriefcaseIcon,
  "/dashboard/advisor": CompassIcon,
  "/dashboard/watchlist": EyeIcon,
  "/settings": GearIcon,
};

type IconComponent = (props: {
  className?: string;
  strokeWidth?: number;
}) => JSX.Element;

function iconFor(item: NavItem): IconComponent {
  return NAV_ICONS[item.href] ?? BriefcaseIcon;
}

function isActive(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

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
        {MOBILE_NAV_ITEMS.map((item) => {
          const active = isActive(pathname, item.href);
          const Icon = iconFor(item);
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={active ? "page" : undefined}
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
              <Icon
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
      <Link href="/dashboard/positions" className="block mb-8 group">
        <span className="block font-display text-base font-normal text-text-primary leading-none">
          Portfolio
        </span>
        <span className="block text-[10px] uppercase tracking-widest2 text-text-muted mt-0.5 opacity-50 group-hover:opacity-70 transition-opacity duration-160">
          Intelligence
        </span>
      </Link>

      {/* Primary navigation — exactly the three canonical views */}
      <div className="space-y-px">
        {DESKTOP_NAV_ITEMS.map((item) => {
          const active = isActive(pathname, item.href);
          const Icon = iconFor(item);
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={active ? "page" : undefined}
              className={cn(
                "flex items-center gap-3 py-2 text-sm transition-colors duration-160",
                // Engraved active rule: 2pt left border + very subtle accent bg
                active
                  ? "text-accent font-medium bg-accent/[0.06] pl-2.5 pr-3 border-l-2 border-accent"
                  : "text-text-secondary hover:text-text-primary hover:bg-surface-elevated/60 px-3 border-l-2 border-transparent"
              )}
            >
              <Icon
                className="w-4 h-4 shrink-0"
                strokeWidth={active ? 2 : 1.5}
              />
              <span className="text-sm">{item.label}</span>
            </Link>
          );
        })}
      </div>

      {/* Footer chrome — secondary actions, visually separated from primary nav */}
      <div className="mt-auto pt-3 border-t border-border-subtle/40">
        {SECONDARY_NAV_ITEMS.map((item) => {
          const active = isActive(pathname, item.href);
          const Icon = iconFor(item);
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={active ? "page" : undefined}
              className={cn(
                "flex items-center gap-2 px-1 py-1.5 text-[11px] uppercase tracking-label transition-colors duration-160",
                active
                  ? "text-accent"
                  : "text-text-muted opacity-60 hover:opacity-100 hover:text-text-secondary"
              )}
            >
              <Icon className="w-3.5 h-3.5 shrink-0" strokeWidth={1.5} />
              <span>{item.label}</span>
            </Link>
          );
        })}
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

function CompassIcon({
  className,
  strokeWidth = 1.5,
}: {
  className?: string;
  strokeWidth?: number;
}) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={strokeWidth}>
      <circle cx="12" cy="12" r="10" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M16.24 7.76l-2.12 6.36-6.36 2.12 2.12-6.36 6.36-2.12z" strokeLinecap="round" strokeLinejoin="round" />
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
      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="12" cy="12" r="3" strokeLinecap="round" strokeLinejoin="round" />
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
