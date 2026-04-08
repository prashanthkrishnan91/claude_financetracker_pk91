import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: [
    "./src/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Robinhood-inspired dark palette
        background: "#07090f",
        surface: "#0f1117",
        "surface-elevated": "#161b22",
        border: "#21262d",
        "text-primary": "#e8eaed",
        "text-secondary": "#8b949e",
        "text-muted": "#6e7681",
        accent: "#00e676",        // Robinhood green
        "accent-hover": "#00c853",
        danger: "#ff5252",
        warning: "#ffd740",
        info: "#448aff",
        // P&L colors
        "pnl-positive": "#00e676",
        "pnl-negative": "#ff5252",
      },
      fontFamily: {
        display: ["DM Serif Display", "Georgia", "serif"],
        mono: ["JetBrains Mono", "Fira Code", "monospace"],
        sans: ["Inter", "system-ui", "sans-serif"],
      },
      borderRadius: {
        lg: "12px",
        md: "8px",
        sm: "6px",
      },
    },
  },
  plugins: [],
};

export default config;
