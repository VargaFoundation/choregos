// SPDX-License-Identifier: Apache-2.0
import { readFile } from "node:fs/promises";
import { join } from "node:path";

/**
 * Files of the sample theme, for the demo build only (S39-03b): in a live console the routes answer
 * 404 and the real files come from the API under `/api/v1/` through the rewrite. The font is Geist's
 * own variable file, which the console already ships; the marks are a tiny SVG.
 */
const HEADERS = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" };
const NOT_FOUND = () => new Response("not found", { status: 404 });
const demo = () => process.env.NEXT_PUBLIC_API_MODE === "mock";

export function markResponse(): Response {
  if (!demo()) return NOT_FOUND();
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20"><rect x="2" y="2" width="16" height="16" fill="#0b6e4f"/></svg>`;
  return new Response(svg, { headers: { ...HEADERS, "Content-Type": "image/svg+xml" } });
}

export async function fontResponse(): Promise<Response> {
  if (!demo()) return NOT_FOUND();
  const file = join(process.cwd(), "node_modules/geist/dist/fonts/geist-sans/Geist-Variable.woff2");
  try {
    return new Response(new Uint8Array(await readFile(file)), { headers: { ...HEADERS, "Content-Type": "font/woff2" } });
  } catch {
    return NOT_FOUND();
  }
}
