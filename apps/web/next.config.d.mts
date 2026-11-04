// SPDX-License-Identifier: Apache-2.0
// The type of `next.config.mjs` (written in `.mjs`, see its header) for the tests that import it.
import type { NextConfig } from "next";

declare const nextConfig: NextConfig;
export default nextConfig;
