import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        paper: "#FBF7ED",   // warm cream page background
        ink: "#111111",     // near-black borders/text
        accent: {
          yellow: "#FFD23F",
          pink: "#FF4D8D",
          cyan: "#3FC9E0",
          lime: "#A8E80C",
          orange: "#FF7A2F",
          purple: "#7C5CFF",
        },
        // ── Friendly Pop design tokens (Phase 0) — map to app/tokens.css vars.
        //    NEW keys only; the brutalist palette above is untouched so no
        //    current screen changes until primitives adopt these in Phase 1. ──
        primary: "var(--color-primary)",
        "primary-press": "var(--color-primary-press)",
        "on-primary": "var(--color-on-primary)",
        secondary: "var(--color-secondary)",
        "on-secondary": "var(--color-on-secondary)",
        highlight: "var(--color-accent)",
        "on-highlight": "var(--color-on-accent)",
        success: "var(--color-success)",
        warning: "var(--color-warning)",
        danger: "var(--color-danger)",
        canvas: "var(--color-bg)",
        surface: "var(--color-surface)",
        "surface-alt": "var(--color-surface-alt)",
        "on-surface": "var(--color-on-surface)",
        "on-surface-muted": "var(--color-on-surface-muted)",
        outline: "var(--color-outline)",
      },
      borderRadius: {
        control: "var(--radius-md)",
        card: "var(--radius-lg)",
        xl2: "var(--radius-xl)",
        pill: "var(--radius-pill)",
      },
      boxShadow: {
        // Hard offset shadows — no blur. The brutalist signature (kept).
        "brutal-sm": "2px 2px 0 0 #111111",
        brutal: "4px 4px 0 0 #111111",
        "brutal-lg": "6px 6px 0 0 #111111",
        "brutal-white": "4px 4px 0 0 #ffffff",
        // Soft elevation for the Friendly Pop system (Phase 0 tokens).
        "elev-1": "var(--elev-1)",
        "elev-2": "var(--elev-2)",
        "elev-3": "var(--elev-3)",
      },
      fontFamily: {
        display: ["var(--font-display)"],
        ui: ["var(--font-sans)"],
      },
      transitionDuration: {
        fast: "var(--dur-fast)",
        base: "var(--dur-base)",
        slow: "var(--dur-slow)",
      },
      transitionTimingFunction: {
        pop: "var(--ease-out)",
        smooth: "var(--ease-in-out)",
      },
    },
  },
  plugins: [],
};

export default config;
