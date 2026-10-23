import { defineConfig, devices } from "@playwright/test";

/**
 * Live journeys: the console against the real API, not its mock (S40-17c). The API runs on fakes
 * with a throwaway SQLite database (`tools/e2e_live_api.py`), the console in `next dev` with its
 * `/api/v1` rewritten to it. `pnpm e2e:live` (needs `uv` and Playwright's Chromium); CI runs it in
 * the `web-live` job (S40-17d).
 */
const port = process.env.PORT ?? "3100";
const apiPort = process.env.API_PORT ?? "8765";

export default defineConfig({
  testDir: "./e2e-live",
  timeout: 90_000,
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: `http://127.0.0.1:${port}`, trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: `uv run python tools/e2e_live_api.py ${apiPort} http://127.0.0.1:${port}`,
      cwd: "../..",
      url: `http://127.0.0.1:${apiPort}/healthz`,
      reuseExistingServer: true,
      timeout: 120_000,
    },
    {
      command: `pnpm dev -H 127.0.0.1 -p ${port}`,
      env: { CHOREGOS_API_URL: `http://127.0.0.1:${apiPort}` },
      url: `http://127.0.0.1:${port}`,
      reuseExistingServer: true,
      timeout: 180_000,
    },
  ],
});
