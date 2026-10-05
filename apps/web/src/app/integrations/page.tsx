// SPDX-License-Identifier: Apache-2.0
"use client";

import { IntegrationsPage } from "@/components/integrations/page";

/** Integrations, sans client choisi : Claude Code d'abord (décision du 2026-10-05). */
export default function Page() {
  return <IntegrationsPage client="claude-code" />;
}
