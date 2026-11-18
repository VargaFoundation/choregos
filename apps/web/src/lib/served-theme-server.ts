// SPDX-License-Identifier: Apache-2.0
/**
 * The instance theme, read by the root layout on the server before anything is drawn
 * (S39-03, ADR 0064). Never throws and never waits long: a theme must not be able to break, or
 * slow, the console. Any failure gives the default look.
 *
 * The theme is the instance's, or, when the person remembered an organisation (the cookie
 * `choregos_org`, S42-13), that organisation's: `GET /api/v1/theme?org=<slug>`, a public route, so the
 * cookie only selects a look and never widens what anyone can read or do.
 *
 * On a host an organisation owns (ADR 0175) the host decides: the visitor's host is forwarded to the API
 * (`X-Forwarded-Host`), which serves that organisation's theme whatever the cookie says and names the
 * organisation (`X-Choregos-Host-Org`); a tenant host's memo entry is keyed by the host alone.
 */
import { cookies, headers } from "next/headers";
import { ORG_COOKIE, validOrgSlug } from "./remembered-org";
import { DEFAULT_SERVED_THEME, sanitizeTheme, type ServedTheme } from "./served-theme";
import { validHost } from "./tenant-host";

const TIMEOUT_MS = 1500;
const MEMO_MS = 60_000;
/** A cookie is client-controlled: the memo cannot grow with the values a client invents. */
const MEMO_MAX = 32;

/** What the first paint needs: the theme, and the organisation the visitor's host belongs to (or null). */
export type ServedLook = { theme: ServedTheme; hostOrg: string | null };

/**
 * One entry per organisation ("" is the instance), so one person's look never serves another's; a tenant
 * host has its own entry, keyed `host:<name>`, which the cookie cannot reach.
 */
const memo = new Map<string, { at: number } & ServedLook>();

/** Test hook: forget the memo. */
export function forgetTheTheme(): void {
  memo.clear();
}

function remember(key: string, at: number, look: ServedLook): void {
  if (!memo.has(key) && memo.size >= MEMO_MAX) {
    for (const [other, entry] of memo) if (at - entry.at >= MEMO_MS) memo.delete(other);
    // Still full: drop the oldest (a Map iterates in insertion order).
    if (memo.size >= MEMO_MAX) memo.delete(memo.keys().next().value as string);
  }
  memo.set(key, { at, ...look });
}

async function mockTheme(org: string | null): Promise<ServedTheme> {
  // The demo build has no API: a cookie picks a fixture, so a Playwright run can see a theme.
  const choice = (await cookies()).get("choregos_mock_theme")?.value;
  const { SAMPLE_THEME, SAMPLE_ORG_THEMES } = await import("@/mocks/theme-sample");
  if (org && org in SAMPLE_ORG_THEMES) return SAMPLE_ORG_THEMES[org]!;
  return choice === "sample" ? SAMPLE_THEME : DEFAULT_SERVED_THEME;
}

/** The host the visitor reached the console on: `X-Forwarded-Host` (the ingress) or `Host`; null outside a request. */
async function visitorHost(): Promise<string | null> {
  try {
    const incoming = await headers();
    return validHost(incoming.get("x-forwarded-host")) ?? validHost(incoming.get("host"));
  } catch {
    return null;
  }
}

export async function servedLook(): Promise<ServedLook> {
  const org = validOrgSlug((await cookies()).get(ORG_COOKIE)?.value);
  if (process.env.NEXT_PUBLIC_API_MODE === "mock") return { theme: await mockTheme(org), hostOrg: null };
  const host = await visitorHost();
  const now = Date.now();
  const tenantKey = host ? `host:${host}` : null;
  const key = `${host ?? ""}|${org ?? ""}`;
  for (const candidate of tenantKey ? [tenantKey, key] : [key]) {
    const hit = memo.get(candidate);
    if (hit && now - hit.at < MEMO_MS) return { theme: hit.theme, hostOrg: hit.hostOrg };
  }
  const base = process.env.TRANIBOR_API_URL ?? "http://localhost:8000";
  const query = org ? `?org=${encodeURIComponent(org)}` : "";
  try {
    const response = await fetch(`${base}/api/v1/theme${query}`, {
      cache: "no-store",
      headers: host ? { "x-forwarded-host": host } : {},
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    const theme = response.ok ? sanitizeTheme(await response.json()) : DEFAULT_SERVED_THEME;
    const hostOrg = response.ok ? validOrgSlug(response.headers.get("x-choregos-host-org")) : null;
    // A failure is memoised too: a down API must not cost 1.5 s on every page.
    remember(hostOrg && tenantKey ? tenantKey : key, now, { theme, hostOrg });
    return { theme, hostOrg };
  } catch {
    remember(key, now, { theme: DEFAULT_SERVED_THEME, hostOrg: null });
    return { theme: DEFAULT_SERVED_THEME, hostOrg: null };
  }
}

export async function instanceTheme(): Promise<ServedTheme> {
  return (await servedLook()).theme;
}
