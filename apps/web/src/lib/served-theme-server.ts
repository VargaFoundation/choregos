// SPDX-License-Identifier: Apache-2.0
/**
 * The instance theme, read by the root layout on the server before anything is drawn
 * (S39-03, ADR 0064). Never throws and never waits long: a theme must not be able to break, or
 * slow, the console. Any failure gives the default look.
 */
import { cookies } from "next/headers";
import { DEFAULT_SERVED_THEME, sanitizeTheme, type ServedTheme } from "./served-theme";

const TIMEOUT_MS = 1500;
const MEMO_MS = 60_000;

let memo: { at: number; theme: ServedTheme } | null = null;

/** Test hook: forget the memo. */
export function forgetTheTheme(): void {
  memo = null;
}

async function mockTheme(): Promise<ServedTheme> {
  // The demo build has no API: a cookie picks a fixture, so a Playwright run can see a theme.
  const choice = (await cookies()).get("choregos_mock_theme")?.value;
  if (choice !== "sample") return DEFAULT_SERVED_THEME;
  return (await import("@/mocks/theme-sample")).SAMPLE_THEME;
}

export async function instanceTheme(): Promise<ServedTheme> {
  if (process.env.NEXT_PUBLIC_API_MODE === "mock") return mockTheme();
  const now = Date.now();
  if (memo && now - memo.at < MEMO_MS) return memo.theme;
  const base = process.env.CHOREGOS_API_URL ?? "http://localhost:8000";
  try {
    const response = await fetch(`${base}/api/v1/theme`, {
      cache: "no-store",
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    const theme = response.ok ? sanitizeTheme(await response.json()) : DEFAULT_SERVED_THEME;
    // A failure is memoised too: a down API must not cost 1.5 s on every page.
    memo = { at: now, theme };
    return theme;
  } catch {
    memo = { at: now, theme: DEFAULT_SERVED_THEME };
    return DEFAULT_SERVED_THEME;
  }
}
