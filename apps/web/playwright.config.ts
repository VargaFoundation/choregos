import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end journeys of the front end. In CI, the server runs in demo mode
 * (`NEXT_PUBLIC_API_MODE=mock`) so that the journeys can be tested without an API; the
 * end-to-end scenarios against the real API live in `tests/e2e/` at the root.
 */
// `PORT`: a machine where a `next dev` already holds 3000 runs the journeys on another port.
const port = process.env.PORT ?? "3000";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  fullyParallel: true,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : [["list"]],
  use: { baseURL: `http://127.0.0.1:${port}`, trace: "on-first-retry" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `NEXT_PUBLIC_API_MODE=mock pnpm build && NEXT_PUBLIC_API_MODE=mock pnpm start -p ${port}`,
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
  },
});
