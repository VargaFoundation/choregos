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
import { A2A_ENTRY, estClient } from "@/lib/integrations";
import { useSession } from "@/lib/session";
import { A2aIntegration } from "./a2a";
import { ClientTabs, IntegrationsPanel } from "./panel";

/**
 * The Integrations page, at the scale of the organisation or of a project (ADR 0030).
 *
 * "How do I connect to Choregos from my Claude?" The product owner's question on
 * 2026-10-05. The answer fits on this page: a tab per client, a token minted for it,
 * the snippet to paste, and the finding that it connected.
 */
export function IntegrationsPage({ client, projet }: { client: string; projet?: string }) {
  const base = projet ? `/p/${projet}/integrations` : "/integrations";
  const { org } = useSession();
  return (
    <div className="space-y-6">
      {!projet && (
        <div className="space-y-2">
          <OrgPageHeader title="Integrations" description="Plug Claude, any MCP client, an application or another agent into Choregos. Your agent lists your projects, opens and follows work items, and tells you what waits for your decision — with your rights, never more. It never decides: you do, in the console." />
          <Glossary ici="clients" />
        </div>
      )}
      {client === A2A_ENTRY.id ? (
        // An application or another agent (S62-11): a page of steps, not an MCP client's snippet.
        <div className="space-y-6">
          <ClientTabs client={client} base={base} />
          <A2aIntegration org={org} projet={projet} />
        </div>
      ) : estClient(client) ? (
        <IntegrationsPanel client={client} projet={projet} base={base} />
      ) : (
        <Empty title="Unknown client">No client called {client}</Empty>
      )}
      {!projet && client !== A2A_ENTRY.id && <OrganisationClients />}
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
      <AiClientCatalogue org={org} />
    </>
  );
}
