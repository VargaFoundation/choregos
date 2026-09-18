import type { Config } from "tailwindcss";

/** Thème sobre et dense, orienté opérateur : peu de couleurs, beaucoup d'information. */
export default {
  content: ["./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        surface: { DEFAULT: "rgb(var(--surface) / <alpha-value>)", muted: "rgb(var(--surface-muted) / <alpha-value>)" },
        ink: { DEFAULT: "rgb(var(--ink) / <alpha-value>)", muted: "rgb(var(--ink-muted) / <alpha-value>)" },
        line: "rgb(var(--line) / <alpha-value>)",
        agent: "rgb(var(--agent) / <alpha-value>)",
        human: "rgb(var(--human) / <alpha-value>)",
        system: "rgb(var(--system) / <alpha-value>)",
        danger: "rgb(var(--danger) / <alpha-value>)",
        ok: "rgb(var(--ok) / <alpha-value>)",
        warn: "rgb(var(--warn) / <alpha-value>)",
      },
      fontFamily: {
        sans: ["ui-sans-serif", "system-ui", "Inter", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
} satisfies Config;
