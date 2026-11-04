// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { AiClientCatalogue } from "@/components/agents/catalogue";
import { McpClients } from "@/components/agents/mcp-clients";
import { isMcpClient } from "@/components/agents/registry";
import { Glossary } from "@/components/glossary";
import { Empty } from "@/components/ui";
import { OrgPageHeader } from "@/components/org-page-header";
import { api } from "@/lib/api";
import { estClient } from "@/lib/integrations";
import { useSession } from "@/lib/session";
import { A2aAgent } from "./a2a";
import { IntegrationsPanel } from "./panel";

/**
 * The Integrations page, at the scale of the organisation or of a project (ADR 0030).
 *
 * "How do I connect to Choregos from my Claude?" The product owner's question on
 * 2026-10-05. The answer fits on this page: a tab per client, a token minted for it,
 * the snippet to paste, and the finding that it connected.
 */
export function IntegrationsPage({ client, projet }: { client: string; projet?: string }) {
  const base = projet ? `/p/${projet}/integrations` : "/integrations";
  return (
    <div className="space-y-6">
      {!projet && (
        <div className="space-y-2">
          <OrgPageHeader title="Integrations" description="Plug Claude, or any MCP client, into Choregos. Your agent lists your projects, opens and follows work items, and tells you what waits for your decision — with your rights, never more. It never decides: you do, in the console." />
          <Glossary ici="clients" />
        </div>
      )}
      {estClient(client) ? (
        <IntegrationsPanel client={client} projet={projet} base={base} />
      ) : (
        <Empty title="Unknown client">No client called {client}</Empty>
      )}
      {!projet && <OrganisationClients />}
    </div>
  );
}

/**
 * Everything about AI clients lives on the Integrations page (S30-04, plan item 7): the caller's clients — and the
 * external agent each acts as — and the assistants that connect in one click. The agents' page is
 * the registry only.
 */
function OrganisationClients() {
  const { org } = useSession();
  const agents = useQuery({ queryKey: ["agents", org], queryFn: () => api.agents(org) });
  const tokens = useQuery({ queryKey: ["tokens"], queryFn: () => api.myTokens() });
  return (
    <>
      <McpClients org={org} clients={(tokens.data ?? []).filter(isMcpClient)} agents={agents.data ?? []} />
      <A2aAgent org={org} />
      <AiClientCatalogue org={org} />
    </>
  );
}
