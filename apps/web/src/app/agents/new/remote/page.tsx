// SPDX-License-Identifier: Apache-2.0
"use client";

import { useRouter } from "next/navigation";
import { ProcedurePanel } from "@/components/procedures/procedure-panel";
import { RemoteAgentForm } from "@/components/remote/remote-agent-form";
import { Breadcrumbs, Empty } from "@/components/ui";
import { OrgPageHeader } from "@/components/org-page-header";
import { hostOf } from "@/lib/procedures";
import { useSession } from "@/lib/session";

/** A remote agent (A2A), registered from its card (ADR 0062, S39-08). */
export default function NewRemoteAgentPage() {
  const router = useRouter();
  const { org } = useSession();
  return (
    <div className="space-y-6">
      <Breadcrumbs steps={[{ href: "/agents", label: "Agents" }, { href: "/agents/new", label: "New agent" }, { label: "Remote agent" }]} />
      <OrgPageHeader
        title="Remote agent (A2A)"
        description="An agent that runs elsewhere, such as Atlassian's Rovo. Paste its card URL: the platform reads it and you choose how to call it."
      />
      {org ? (
        <RemoteAgentForm
          org={org}
          onDone={(agent) => router.push(`/agents/${agent.slug}`)}
          // The generic A2A procedure; the Rovo one as soon as the card address is Atlassian's.
          procedure={(cardUrl) => <ProcedurePanel target="agent:remote.a2a" cardHost={hostOf(cardUrl)} />}
        />
      ) : <Empty>Reading your session…</Empty>}
    </div>
  );
}
