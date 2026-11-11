// SPDX-License-Identifier: Apache-2.0
import { type NextRequest, NextResponse } from "next/server";

/**
 * A CSP without `'unsafe-inline'` for scripts: one nonce per request, which Next sets itself on
 * its scripts (it reads it from the `Content-Security-Policy` header). The price: every page is
 * rendered dynamically, which is already the case of a dashboard that reads the API on every
 * display. Styles keep `'unsafe-inline'`: CodeMirror and React Flow inject `<style>` elements, and
 * a style runs nothing. No third-party origin: the YAML editor (Monaco) was downloaded from a CDN,
 * whose stylesheet and font this policy blocked, so it showed unstyled (review of 07/10).
 * Everything the console loads comes from itself.
 *
 * Before 2026-09-25: `script-src 'self' 'unsafe-inline'` in `next.config.mjs`, a CSP that let
 * through exactly what a CSP exists to stop.
 */
/** The embedded component's page (S63-12, ADR 0139 point 6): the one route a host may frame. */
const EMBED_PATH = /^\/embed\/assistants\/([a-z0-9][a-z0-9_-]*)\/([a-z0-9][a-z0-9-]{0,62})\/?$/;

/**
 * The origins that may frame an installation's embed page: its `config.embed_origins`, read from the
 * API's public route (the visitor may not be signed in yet, and the cookie of a framed page is not
 * ours to rely on). Any failure frames nobody: `'none'`, the console's rule for every other page.
 */
async function embedOrigins(org: string, slug: string): Promise<string[]> {
  const apiUrl = process.env.CHOREGOS_API_URL ?? "http://localhost:8000";
  try {
    const response = await fetch(`${apiUrl}/api/v1/public/assistants/${org}/${slug}/embed`, {
      cache: "no-store",
      signal: AbortSignal.timeout(3000),
    });
    if (!response.ok) return [];
    const body = (await response.json()) as { embed_origins?: unknown };
    return Array.isArray(body.embed_origins)
      ? body.embed_origins.filter((o): o is string => typeof o === "string" && /^https?:\/\/[^\s;,'"*]+$/.test(o))
      : [];
  } catch {
    return [];
  }
}

/** Every page: framed by nobody. The embed page: framed by its installation's origins, resolved first. */
export function proxy(request: NextRequest): NextResponse | Promise<NextResponse> {
  const embed = EMBED_PATH.exec(request.nextUrl.pathname);
  if (!embed) return respond(request, "'none'", false);
  return embedOrigins(embed[1]!, embed[2]!).then((origins) =>
    respond(request, origins.length > 0 ? origins.join(" ") : "'none'", true),
  );
}

function respond(request: NextRequest, frameAncestors: string, embed: boolean): NextResponse {
  const nonce = Buffer.from(crypto.randomUUID()).toString("base64");
  const apiUrl = process.env.CHOREGOS_API_URL ?? "http://localhost:8000";
  const isDev = process.env.NODE_ENV === "development";
  const csp = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${isDev ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline'",
    // `blob:`: an image artifact is shown from the bytes the API served, digest checked (ADR 0049).
    "img-src 'self' data: blob:",
    "font-src 'self'",
    // A video artifact plays, and a PDF one opens in the browser's viewer, from their bytes (S31-06).
    "media-src 'self' blob:",
    "frame-src 'self' blob:",
    `connect-src 'self' ${apiUrl}`,
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    `frame-ancestors ${frameAncestors}`,
  ].join("; ");

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", csp);
  // The layout draws no chrome around the embedded component.
  if (embed) requestHeaders.set("x-embed", "1");
  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", csp);
  return response;
}

export const config = {
  matcher: [
    {
      // Everything except static resources and `next/link` prefetches.
      source: "/((?!api|mcp|\\.well-known|_next/static|_next/image|favicon.ico).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
