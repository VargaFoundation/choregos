// SPDX-License-Identifier: Apache-2.0
/**
 * A neutral sample theme for the demo build and the Playwright run (S39-03b). Deliberately not any
 * real brand: the community edition is public, and a brand is a deployment's data.
 */
import type { ServedTheme } from "@/lib/served-theme";

export const SAMPLE_THEME: ServedTheme = {
  id: "sample",
  product_name: "Sample",
  default_mode: "light",
  tokens: {
    light: {
      surface: "#f7f9f8",
      sidebar: "#eef2f0",
      accent: "#0b6e4f",
      "accent-hover": "#095a40",
      "accent-strong": "#0b6e4f",
      focus: "#0b6e4f",
    },
    dark: { accent: "#1f7a5a", "accent-hover": "#18654a", "accent-strong": "#5fd3a6", focus: "#5fd3a6" },
  },
  radius: { control: 2, surface: 4 },
  fonts: {
    title: {
      family: "Sample Serif",
      weight: 300,
      faces: [{ src: "/api/v1/mock-theme-assets/sample-serif.woff2", weight: 300, style: "normal" }],
    },
  },
  logo: "/api/v1/mock-theme-assets/logo.svg",
  favicon: "/api/v1/mock-theme-assets/favicon.svg",
  footer: { text: "Operated by Sample" },
};
