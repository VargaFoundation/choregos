// SPDX-License-Identifier: Apache-2.0
"use client";

import { Glossaire } from "@/components/glossaire";
import { Empty, Eyebrow, Heading } from "@/components/ui";
import { estClient } from "@/lib/integrations";
import { IntegrationsPanel } from "./panel";

/**
 * La page Integrations, à l'échelle de l'organisation ou d'un projet (ADR 0030).
 *
 * « Comment je me connecte à Choregos depuis mon Claude ? » — la question du product owner le
 * 2026-10-05. La réponse tient sur cette page : un onglet par client, un jeton frappé pour lui,
 * l'extrait à coller, et le constat qu'il s'est connecté.
 */
export function IntegrationsPage({ client, projet }: { client: string; projet?: string }) {
  const base = projet ? `/p/${projet}/integrations` : "/integrations";
  return (
    <div className="space-y-6">
      {!projet && (
        <div className="space-y-2">
          <Eyebrow>integrations · MCP</Eyebrow>
          <Heading as="h1" size="xl">
            connect a client
          </Heading>
          <p className="max-w-3xl text-sm text-ink-muted">
            Plug Claude, or any MCP client, into Choregos. Your agent lists your projects, opens and follows work
            items, and tells you what waits for your decision — with your rights, never more. It never decides: you
            do, in the console.
          </p>
          <Glossaire ici="clients" />
        </div>
      )}
      {estClient(client) ? (
        <IntegrationsPanel client={client} projet={projet} base={base} />
      ) : (
        <Empty title="unknown client">no client called {client}</Empty>
      )}
    </div>
  );
}
