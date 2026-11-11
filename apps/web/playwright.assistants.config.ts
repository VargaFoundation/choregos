import { defineConfig, devices } from "@playwright/test";

/**
 * The assistants' screens in a real browser (S63-18): `/assistants`, a conversation, the owner's installation
 * form. The console runs in LIVE mode against a fake API (`e2e-assistants/fake-api.mjs`).
 * Run: `pnpm e2e:assistants`.
 */
const console_ = process.env.ASSISTANTS_CONSOLE_PORT ?? "3400";

export default defineConfig({
  testDir: "./e2e-assistants",
  timeout: 30_000,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : [["list"]],
  use: { baseURL: `http://127.0.0.1:${console_}`, trace: "on-first-retry" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    { command: "node e2e-assistants/fake-api.mjs", url: "http://127.0.0.1:4200/api/v1/me", reuseExistingServer: !process.env.CI },
    {
      command: `CHOREGOS_API_URL=http://127.0.0.1:4200 pnpm build && CHOREGOS_API_URL=http://127.0.0.1:4200 pnpm start -p ${console_}`,
      url: `http://127.0.0.1:${console_}/login`,
      reuseExistingServer: !process.env.CI,
      timeout: 240_000,
    },
  ],
});
