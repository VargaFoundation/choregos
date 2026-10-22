// SPDX-License-Identifier: Apache-2.0
/**
 * The instance theme, read by the root layout on the server before anything is drawn
 * (S39-03, ADR 0064). Never throws and never waits long: a theme must not be able to break, or
 * slow, the console. Any failure gives the default look.
 *
 * The theme is the instance's, or, when the person remembered an organisation (the cookie
 * `choregos_org`, S42-13), that organisation's: `GET /api/v1/theme?org=<slug>`, a public route, so the
 * cookie only selects a look and never widens what anyone can read or do.
 */
import { cookies } from "next/headers";
import { ORG_COOKIE, validOrgSlug } from "./remembered-org";
import { DEFAULT_SERVED_THEME, sanitizeTheme, type ServedTheme } from "./served-theme";

const TIMEOUT_MS = 1500;
const MEMO_MS = 60_000;
/** A cookie is client-controlled: the memo cannot grow with the values a client invents. */
const MEMO_MAX = 32;

/** One entry per organisation ("" is the instance), so one person's look never serves another's. */
const memo = new Map<string, { at: number; theme: ServedTheme }>();

/** Test hook: forget the memo. */
export function forgetTheTheme(): void {
  memo.clear();
}

function remember(key: string, at: number, theme: ServedTheme): void {
  if (!memo.has(key) && memo.size >= MEMO_MAX) {
    for (const [other, entry] of memo) if (at - entry.at >= MEMO_MS) memo.delete(other);
    // Still full: drop the oldest (a Map iterates in insertion order).
    if (memo.size >= MEMO_MAX) memo.delete(memo.keys().next().value as string);
  }
  memo.set(key, { at, theme });
}

async function mockTheme(org: string | null): Promise<ServedTheme> {
  // The demo build has no API: a cookie picks a fixture, so a Playwright run can see a theme.
  const choice = (await cookies()).get("choregos_mock_theme")?.value;
  const { SAMPLE_THEME, SAMPLE_ORG_THEMES } = await import("@/mocks/theme-sample");
  if (org && org in SAMPLE_ORG_THEMES) return SAMPLE_ORG_THEMES[org]!;
  return choice === "sample" ? SAMPLE_THEME : DEFAULT_SERVED_THEME;
}

export async function instanceTheme(): Promise<ServedTheme> {
  const org = validOrgSlug((await cookies()).get(ORG_COOKIE)?.value);
  if (process.env.NEXT_PUBLIC_API_MODE === "mock") return mockTheme(org);
  const key = org ?? "";
  const now = Date.now();
  const hit = memo.get(key);
  if (hit && now - hit.at < MEMO_MS) return hit.theme;
  const base = process.env.CHOREGOS_API_URL ?? "http://localhost:8000";
  const query = org ? `?org=${encodeURIComponent(org)}` : "";
  try {
    const response = await fetch(`${base}/api/v1/theme${query}`, {
      cache: "no-store",
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    const theme = response.ok ? sanitizeTheme(await response.json()) : DEFAULT_SERVED_THEME;
    // A failure is memoised too: a down API must not cost 1.5 s on every page.
    remember(key, now, theme);
    return theme;
  } catch {
    remember(key, now, DEFAULT_SERVED_THEME);
    return DEFAULT_SERVED_THEME;
  }
}
