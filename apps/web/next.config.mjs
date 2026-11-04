/**
 * The front end talks to the Choregos API only. In development, `/api/v1` is relayed to the
 * local API (or to the Prism mock) to avoid any CORS configuration.
 *
 * In `.mjs` and not `.ts`: `next start` loads this file at startup, and for a `.ts` it needs
 * the `typescript` package. The production image ships only the `dependencies`, so it tried to
 * install TypeScript when the container started, and failed on "Failed to load
 * next.config.ts". The JSDoc below keeps the typing in the editor and in `tsc`.
 */
const apiUrl = process.env.CHOREGOS_API_URL ?? "http://localhost:8000";

/** @type {import("next").NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  typedRoutes: false,
  experimental: {
    optimizePackageImports: ["@tanstack/react-query"],
    // Turbopack merged every module under 200 kB into the chunk of each route that used it: the
    // glossary, the integrations page, the confirmation dialog or the schema form were shipped four
    // to nine times — 160 kB of copies, measured on 2026-10-13. Small modules now stay in chunks of
    // their own, shared by the routes: a page loads a few more files, of the same total size, and
    // the build ships about 145 kB less (bundle budget, `scripts/bundle-budget.mjs`).
    turbopackChunking: { maxMergeChunkSize: 5_000, minChunkSize: 5_000 },
  },
  // A live build does not ship the demo fixtures: the bundler emits the chunk of every `import()`
  // it sees, even behind `IS_MOCK` — 80 kB of fake data in every live console. The demo build
  // (`NEXT_PUBLIC_API_MODE=mock`) keeps them.
  turbopack:
    process.env.NEXT_PUBLIC_API_MODE === "mock"
      ? {}
      : { resolveAlias: { "@/mocks/data": "./src/mocks/absent.ts" } },
  async rewrites() {
    return [
      { source: "/api/v1/:path*", destination: `${apiUrl}/api/v1/:path*` },
      // The MCP gate of external clients (ADR 0030). In production, the Ingress sends it to the API;
      // in development, the relay avoids having two origins to configure in a client.
      { source: "/mcp", destination: `${apiUrl}/mcp` },
      { source: "/mcp/:path*", destination: `${apiUrl}/mcp/:path*` },
      // The A2A door (ADR 0078): an outside agent's card and task calls.
      { source: "/a2a/:path*", destination: `${apiUrl}/a2a/:path*` },
      // Its OAuth metadata (RFC 9728), read by claude.ai before it connects.
      {
        source: "/.well-known/oauth-protected-resource/:path*",
        destination: `${apiUrl}/.well-known/oauth-protected-resource/:path*`,
      },
    ];
  },
  // An ontology proposal has been a core action since S20-08, under the SAME identifier: the
  // "proposals" page merged into the actions page (S20-10), and a decision link issued before —
  // through the MCP gate, in an email — still leads to the right page.
  async redirects() {
    return [
      { source: "/p/:slug/proposals", destination: "/p/:slug/actions", permanent: true },
      { source: "/p/:slug/proposals/:id", destination: "/p/:slug/actions/:id", permanent: true },
      // The decisions box became the inbox (S23-02): tickets AND actions that wait for someone.
      { source: "/approvals", destination: "/inbox", permanent: true },
      // The workflow catalogue is the workflow library (S55-04): the word read as French.
      { source: "/p/:slug/workflows/catalogue", destination: "/p/:slug/workflows/library", permanent: true },
      // The organisation's catalogues followed (S55-15): MCP servers, connector types, plugins and the
      // skills one live under `library`; a sub-path and the query (`?q=`) are kept.
      { source: "/catalogue/:path*", destination: "/library/:path*", permanent: true },
      { source: "/skills/catalogue/:path*", destination: "/skills/library/:path*", permanent: true },
      // The administration's path of the MCP catalogue (ADR 0074), a page that only redirected until S55-15.
      { source: "/admin/connectors/catalogue", destination: "/library/mcp-servers", permanent: true },
      // A workflow opens on its graph (S55-06): the map is the workflow's page, its query (`?view=list`) kept.
      { source: "/p/:slug/workflows/:name/map", destination: "/p/:slug/workflows/:name", permanent: true },
    ];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          // The Content-Security-Policy is set by `src/proxy.ts`, with one nonce per request.
        ],
      },
    ];
  },
};

export default nextConfig;
