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
export function proxy(request: NextRequest) {
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
    "frame-ancestors 'none'",
  ].join("; ");

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", csp);
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
