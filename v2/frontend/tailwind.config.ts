import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: [
    "./src/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // ── Obsidian dark palette ─────────────────────────────────────────
        // bg.canvas: near-black with a faint green-blue cast
        background: "#0A0B0F",
        surface: "#10131A",
        "surface-elevated": "#161A23",
        "surface-high": "#1D222C",
        "surface-hover": "#1D222C",   // alias for surface-high (backward compat)
        "surface-peak": "#262C38",

        // ── Border tokens ─────────────────────────────────────────────────
        "border-subtle": "#1F2531",
        border: "#2A3140",
        "border-strong": "#3A4253",

        // ── Text ──────────────────────────────────────────────────────────
        "text-primary": "#E8EAED",
        "text-secondary": "#8b949e",
        "text-muted": "#6e7681",

        // ── Signature accent — Atelier Green ──────────────────────────────
        // Replaces neon #00e676. Used for Buy, primary affordance, brand.
        accent: "#2EC27E",
        "accent-hover": "#238C5E",

        // ── Secondary accents (semantic only, never decorative) ────────────
        "accent-lapis": "#5B7CFF",    // Hold / info / AI-composed mark
        "accent-blue": "#5B7CFF",     // backward compat alias
        "accent-plum": "#B47EFF",     // Review / needs human attention
        "accent-purple": "#B47EFF",   // backward compat alias
        "accent-saffron": "#F2A93B",  // Trim / caution
        "accent-crimson": "#D14C5A",  // Sell / risk (oxblood, not Robinhood red)

        // ── Semantic action chips (Buy / Hold / Trim / Sell) ──────────────
        "action-buy": "#2EC27E",
        "action-hold": "#5B7CFF",
        "action-trim": "#F2A93B",
        "action-sell": "#D14C5A",

        // ── Semantic data states ──────────────────────────────────────────
        positive: "#2EC27E",
        negative: "#D14C5A",   // oxblood, not Robinhood red
        caution: "#F2A93B",    // saffron, not neon yellow
        neutral: "#8b949e",

        // ── Legacy token aliases (backward compat) ────────────────────────
        danger: "#D14C5A",
        warning: "#F2A93B",
        info: "#5B7CFF",
        "pnl-positive": "#2EC27E",
        "pnl-negative": "#D14C5A",

        // ── Paper light palette ───────────────────────────────────────────
        // Warm paper, graphite ink, forest accent.
        paper: {
          canvas: "#FAF7F2",
          surface: "#FFFFFF",
          elevated: "#F4F1EB",
          "border-subtle": "#E7E1D6",
          "border-default": "#D8D1C2",
          "border-strong": "#B8AE9A",
          accent: "#138659",       // Atelier Green (light)
          hold: "#3955D6",         // Lapis (light)
          trim: "#B97800",         // Saffron (light)
          sell: "#9A2A38",         // Crimson (light)
          "text-primary": "#1A1A2E",
          "text-secondary": "#4A5568",
          "text-muted": "#718096",
        },
      },

      fontFamily: {
        // Resolved via CSS variables set by next/font in layout.tsx.
        // Fallbacks cover SSR before fonts hydrate.
        display: ["var(--font-display)", "DM Serif Display", "Georgia", "serif"],
        sans: ["var(--font-sans)", "Inter", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "JetBrains Mono", "Fira Code", "monospace"],
      },

      fontSize: {
        // ── Design system type scale (4-pt baseline grid) ─────────────────
        "display-xl": ["48px", { lineHeight: "56px", letterSpacing: "-0.02em" }],
        "display-lg": ["36px", { lineHeight: "44px", letterSpacing: "-0.02em" }],
        "display-md": ["28px", { lineHeight: "36px", letterSpacing: "-0.01em" }],
        "headline-lg": ["22px", { lineHeight: "32px", letterSpacing: "-0.01em" }],
        "headline-md": ["18px", { lineHeight: "28px", letterSpacing: "0" }],
        "body-lg": ["16px", { lineHeight: "24px" }],
        "body-md": ["14px", { lineHeight: "22px" }],
        "body-sm": ["13px", { lineHeight: "20px" }],
        caption: ["11px", { lineHeight: "16px", letterSpacing: "0.02em" }],
        // Legacy
        "2xs": ["10px", { lineHeight: "14px", letterSpacing: "0.04em" }],
      },

      letterSpacing: {
        label: "0.08em",
        widest2: "0.12em",
      },

      borderRadius: {
        // Design system radii: 2 / 8 / 12 / 20 / 999
        sharp: "2px",     // data chips, ticker tags, action badges
        sm: "6px",        // kept for backward compat; migrate to sharp/md over time
        md: "8px",        // radius.card — cards
        lg: "12px",       // radius.panel — drawers, panels
        xl: "20px",       // radius.modal — modals, command bar
        pill: "999px",    // radius.pill — status pills, filter pills
        full: "9999px",
      },

      boxShadow: {
        // Almost-flat elevation system — no glow, no drama
        "elev-0": "none",
        "elev-1": "0 1px 2px rgba(0,0,0,0.25)",            // default cards
        "elev-2": "0 6px 24px rgba(0,0,0,0.35)",           // drawers, modals
        "elev-3": "0 24px 48px rgba(0,0,0,0.45)",          // command bar
        // Legacy aliases
        card: "0 1px 2px rgba(0,0,0,0.25)",
        "card-hover": "0 6px 24px rgba(0,0,0,0.35)",
      },

      transitionTimingFunction: {
        // Motion tokens — use via CSS var(--motion-*) in custom CSS
        "motion-enter": "cubic-bezier(0.2, 0.8, 0.2, 1)",
        "motion-exit": "cubic-bezier(0.4, 0.0, 1.0, 1.0)",
        "motion-cross": "cubic-bezier(0.4, 0.0, 0.2, 1.0)",
        "motion-tween": "cubic-bezier(0.25, 0.1, 0.25, 1.0)",
      },

      transitionDuration: {
        "120": "120ms",
        "160": "160ms",
        "240": "240ms",
        "280": "280ms",
        "320": "320ms",
      },
    },
  },
  plugins: [],
};

export default config;
