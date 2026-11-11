import { defineConfig, devices } from "@playwright/test";

/**
 * The embedded component's journey (S63-12): a host application on an allowed origin frames
 * `/embed/assistants/varga/coach`, another one is refused by `frame-ancestors`. The console runs in LIVE mode
 * (the proxy reads the installation's origins from the API), against a fake API (`e2e-embed/fake-servers.mjs`).
 * Run: `pnpm e2e:embed`.
 */
const console_ = process.env.EMBED_CONSOLE_PORT ?? "3300";

export default defineConfig({
  testDir: "./e2e-embed",
  timeout: 30_000,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : [["list"]],
  use: { baseURL: `http://127.0.0.1:${console_}`, trace: "on-first-retry" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    { command: "node e2e-embed/fake-servers.mjs", url: "http://127.0.0.1:4100/api/v1/public/assistants/varga/coach/embed", reuseExistingServer: !process.env.CI },
    {
      command: `CHOREGOS_API_URL=http://127.0.0.1:4100 pnpm build && CHOREGOS_API_URL=http://127.0.0.1:4100 pnpm start -p ${console_}`,
      url: `http://127.0.0.1:${console_}/login`,
      reuseExistingServer: !process.env.CI,
      timeout: 240_000,
    },
  ],
});
