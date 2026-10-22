// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { AiClientCatalogue } from "@/components/agents/catalogue";
import { ClientsMcp } from "@/components/agents/clients-mcp";
import { estUnClientMcp } from "@/components/agents/registre";
import { Glossaire } from "@/components/glossaire";
import { Empty, Heading } from "@/components/ui";
import { api } from "@/lib/api";
import { estClient } from "@/lib/integrations";
import { useSession } from "@/lib/session";
import { A2aAgent } from "./a2a";
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
          <Heading as="h1" size="xl">
            Integrations
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
        <Empty title="Unknown client">No client called {client}</Empty>
      )}
      {!projet && <ClientsDeLOrganisation />}
    </div>
  );
}

/**
 * Everything about AI clients lives on the Integrations page (S30-04, plan item 7): the caller's clients — and the
 * external agent each acts as — and the assistants that connect in one click. The agents' page is
 * the registry only.
 */
function ClientsDeLOrganisation() {
  const { org } = useSession();
  const agents = useQuery({ queryKey: ["agents", org], queryFn: () => api.agents(org) });
  const jetons = useQuery({ queryKey: ["tokens"], queryFn: () => api.myTokens() });
  return (
    <>
      <ClientsMcp org={org} clients={(jetons.data ?? []).filter(estUnClientMcp)} agents={agents.data ?? []} />
      <A2aAgent org={org} />
      <AiClientCatalogue org={org} />
    </>
  );
}
