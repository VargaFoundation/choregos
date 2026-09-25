import { type NextRequest, NextResponse } from "next/server";

/**
 * Une CSP sans `'unsafe-inline'` pour les scripts : un nonce par requête, que Next pose
 * lui-même sur ses scripts (il le lit dans l'en-tête `Content-Security-Policy`). Le prix :
 * chaque page est rendue dynamiquement — c'est déjà le cas d'un tableau de bord qui lit
 * l'API à chaque affichage. Les styles gardent `'unsafe-inline'` : Monaco et React Flow
 * injectent des `<style>`, et un style n'exécute rien.
 *
 * Avant le 2026-09-25 : `script-src 'self' 'unsafe-inline'` dans `next.config.mjs` — une
 * CSP qui laissait passer exactement ce contre quoi une CSP existe.
 */
export function proxy(request: NextRequest) {
  const nonce = Buffer.from(crypto.randomUUID()).toString("base64");
  const apiUrl = process.env.CHOREGOS_API_URL ?? "http://localhost:8000";
  const isDev = process.env.NODE_ENV === "development";
  const csp = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${isDev ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self'",
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
      // Tout sauf les ressources statiques et les préchargements de `next/link`.
      source: "/((?!api|_next/static|_next/image|favicon.ico).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
