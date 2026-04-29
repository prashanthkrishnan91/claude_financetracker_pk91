import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: [
    "./src/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Core dark palette
        background: "#07090f",
        surface: "#0f1117",
        "surface-elevated": "#161b22",
        "surface-hover": "#1a2030",
        border: "#21262d",
        "border-strong": "#30363d",
        "text-primary": "#e8eaed",
        "text-secondary": "#8b949e",
        "text-muted": "#6e7681",

        // Primary accent — neon green (deploy/positive)
        accent: "#00e676",
        "accent-hover": "#00c853",

        // Secondary accents
        "accent-blue": "#60a5fa",   // info / HOLD states
        "accent-purple": "#a78bfa", // REVIEW states

        // Semantic data states (mirrors existing danger/warning/info + new aliases)
        positive: "#00e676",
        negative: "#ff5252",
        caution: "#ffd740",
        neutral: "#8b949e",

        // Legacy token aliases (unchanged — preserves existing usage)
        danger: "#ff5252",
        warning: "#ffd740",
        info: "#448aff",
        "pnl-positive": "#00e676",
        "pnl-negative": "#ff5252",
      },
      fontFamily: {
        display: ["DM Serif Display", "Georgia", "serif"],
        mono: ["JetBrains Mono", "Fira Code", "monospace"],
        sans: ["Inter", "system-ui", "sans-serif"],
      },
      fontSize: {
        "2xs": ["10px", { lineHeight: "14px", letterSpacing: "0.04em" }],
      },
      letterSpacing: {
        label: "0.08em",
        widest2: "0.12em",
      },
      borderRadius: {
        lg: "12px",
        md: "8px",
        sm: "6px",
      },
      boxShadow: {
        card: "0 1px 3px 0 rgba(0,0,0,0.4), 0 1px 2px -1px rgba(0,0,0,0.4)",
        "card-hover": "0 4px 12px 0 rgba(0,0,0,0.5)",
        glow: "0 0 12px rgba(0,230,118,0.15)",
      },
    },
  },
  plugins: [],
};

export default config;
