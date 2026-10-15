// SPDX-License-Identifier: Apache-2.0
"use client";

import { useRouter } from "next/navigation";
import { RemoteAgentForm } from "@/components/remote/remote-agent-form";
import { Breadcrumbs, Empty, Heading, Lead } from "@/components/ui";
import { useSession } from "@/lib/session";

/** A remote agent (A2A), registered from its card (ADR 0062, S39-08). */
export default function NewRemoteAgentPage() {
  const router = useRouter();
  const { org } = useSession();
  return (
    <div className="space-y-6">
      <Breadcrumbs steps={[{ href: "/agents", label: "Agents" }, { href: "/agents/new", label: "New agent" }, { label: "Remote agent" }]} />
      <div className="space-y-2">
        <Heading as="h1" size="xl">
          Remote agent (A2A)
        </Heading>
        <Lead>An agent that runs elsewhere, such as Atlassian&apos;s Rovo. Paste its card URL: the platform reads it and you choose how to call it.</Lead>
      </div>
      {org ? <RemoteAgentForm org={org} onDone={(agent) => router.push(`/agents/${agent.slug}`)} /> : <Empty>Reading your session…</Empty>}
    </div>
  );
}
