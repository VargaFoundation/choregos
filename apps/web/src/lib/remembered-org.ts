// SPDX-License-Identifier: Apache-2.0
/**
 * The organisation the person last chose, remembered in the browser (S42-13).
 *
 * `localStorage` keeps the choice for the client; the non-HttpOnly cookie `choregos_org` carries
 * the same slug to the server, so the root layout can draw that organisation's theme (name, logo,
 * colours) on the first paint, sign-in page included. The cookie only SELECTS a public look: it
 * grants nothing, every API route still checks the session, and an unknown or malformed value
 * means the instance theme.
 */
export const ORG_COOKIE = "choregos_org";
export const ORG_STORAGE_KEY = "choregos.org";

/** The same shape the API accepts for an organisation slug (`theme.ORG_SLUG`). */
const SLUG = /^[a-z0-9][a-z0-9-]{0,62}$/;

/** The slug, or null: anything else (a path, a query, an overlong string) is never trusted. */
export function validOrgSlug(value: unknown): string | null {
  return typeof value === "string" && SLUG.test(value) ? value : null;
}

/** Remembers `slug` for the server (cookie, one year, this site only). */
export function rememberOrgCookie(slug: string): void {
  const safe = validOrgSlug(slug);
  if (!safe || typeof document === "undefined") return;
  const secure = typeof window !== "undefined" && window.location.protocol === "https:" ? "; Secure" : "";
  document.cookie = `${ORG_COOKIE}=${safe}; Path=/; Max-Age=31536000; SameSite=Lax${secure}`;
}

/** The slug remembered by `localStorage`, validated; null without one or without storage. */
export function rememberedOrg(): string | null {
  try {
    return validOrgSlug(window.localStorage.getItem(ORG_STORAGE_KEY));
  } catch {
    return null;
  }
}
