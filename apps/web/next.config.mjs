/**
 * Le front ne parle qu'à l'API Choregos. En développement, `/api/v1` est relayé
 * vers l'API locale (ou vers le mock Prism) pour éviter toute configuration CORS.
 *
 * En `.mjs` et non en `.ts` : `next start` charge ce fichier au démarrage, et pour un `.ts`
 * il lui faut le paquet `typescript`. L'image de production n'embarque que les
 * `dependencies` — elle tentait donc d'installer TypeScript au lancement du conteneur, et
 * échouait sur « Failed to load next.config.ts ». Le JSDoc ci-dessous garde le typage dans
 * l'éditeur et au `tsc`.
 */
const apiUrl = process.env.CHOREGOS_API_URL ?? "http://localhost:8000";

/** @type {import("next").NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  typedRoutes: false,
  // Le design system est livré en TypeScript source : Next le transpile comme le reste.
  transpilePackages: ["@varga/design-system"],
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
      // La porte MCP des clients externes (ADR 0030). En production, l'Ingress l'envoie à l'API ;
      // en développement, le relais évite d'avoir deux origines à configurer dans un client.
      { source: "/mcp", destination: `${apiUrl}/mcp` },
      { source: "/mcp/:path*", destination: `${apiUrl}/mcp/:path*` },
      // Ses métadonnées OAuth (RFC 9728), lues par claude.ai avant de se connecter.
      {
        source: "/.well-known/oauth-protected-resource/:path*",
        destination: `${apiUrl}/.well-known/oauth-protected-resource/:path*`,
      },
    ];
  },
  // Une proposition de l'ontologie est une action du cœur depuis S20-08, sous le MÊME identifiant :
  // la page « proposals » s'est fondue dans celle des actions (S20-10), et un lien de décision émis
  // avant — par la porte MCP, dans un courriel — mène toujours à la bonne page.
  async redirects() {
    return [
      { source: "/p/:slug/proposals", destination: "/p/:slug/actions", permanent: true },
      { source: "/p/:slug/proposals/:id", destination: "/p/:slug/actions/:id", permanent: true },
      // La boîte des décisions est devenue l'inbox (S23-02) : tickets ET actions qui attendent quelqu'un.
      { source: "/approvals", destination: "/inbox", permanent: true },
    ];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          // La Content-Security-Policy est posée par `src/proxy.ts`, avec un nonce par requête.
        ],
      },
    ];
  },
};

export default nextConfig;
