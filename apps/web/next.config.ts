import type { NextConfig } from "next";

/**
 * Le front ne parle qu'à l'API Choregos. En développement, `/api/v1` est relayé
 * vers l'API locale (ou vers le mock Prism) pour éviter toute configuration CORS.
 */
const apiUrl = process.env.CHOREGOS_API_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  typedRoutes: false,
  experimental: { optimizePackageImports: ["@tanstack/react-query"] },
  async rewrites() {
    return [{ source: "/api/v1/:path*", destination: `${apiUrl}/api/v1/:path*` }];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          {
            key: "Content-Security-Policy",
            value:
              "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; " +
              "img-src 'self' data:; connect-src 'self' " + apiUrl + "; frame-ancestors 'none'",
          },
        ],
      },
    ];
  },
};

export default nextConfig;
