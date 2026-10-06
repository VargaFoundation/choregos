// SPDX-License-Identifier: Apache-2.0
// Le type de `next.config.mjs` (écrit en `.mjs`, voir son en-tête) pour les tests qui l'importent.
import type { NextConfig } from "next";

declare const nextConfig: NextConfig;
export default nextConfig;
