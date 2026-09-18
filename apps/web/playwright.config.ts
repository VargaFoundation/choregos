import { defineConfig, devices } from "@playwright/test";

/**
 * Parcours e2e du front. En CI, le serveur tourne en mode démo (`NEXT_PUBLIC_API_MODE=mock`)
 * pour que les parcours soient testables sans API ; les scénarios de bout en bout avec
 * l'API réelle vivent dans `tests/e2e/` à la racine.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  fullyParallel: true,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : [["list"]],
  use: { baseURL: "http://127.0.0.1:3000", trace: "on-first-retry" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "NEXT_PUBLIC_API_MODE=mock pnpm build && NEXT_PUBLIC_API_MODE=mock pnpm start",
    url: "http://127.0.0.1:3000",
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
  },
});
