// SPDX-License-Identifier: Apache-2.0
"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { RUNTIME_LABEL, RuntimeAgentForm, type RuntimeKind } from "@/components/agents/runtime-agent-form";
import { Breadcrumbs, Empty, Heading, Lead } from "@/components/ui";
import { useSession } from "@/lib/session";

const LEAD: Record<RuntimeKind, string> = {
  prompt: "Instructions, a model and tools: the platform runs the loop. No container, no coding agent.",
  voice: "A prompt agent people talk to, through a realtime model. It is spoken to in a session, never run as a step.",
  hosted: "Your own container, built with any framework: from an image, or from a source zip the platform builds.",
};

function kindOf(value: string | null): RuntimeKind {
  return value === "voice" || value === "hosted" ? value : "prompt";
}

/** A prompt, voice or hosted agent (ADR 0080, S41-12). */
export default function NewRuntimeAgentPage() {
  const router = useRouter();
  const { org } = useSession();
  const kind = kindOf(useSearchParams().get("kind"));
  return (
    <div className="space-y-6">
      <Breadcrumbs steps={[{ href: "/agents", label: "Agents" }, { href: "/agents/new", label: "New agent" }, { label: RUNTIME_LABEL[kind] }]} />
      <div className="space-y-2">
        <Heading as="h1" size="xl">
          {RUNTIME_LABEL[kind]}
        </Heading>
        <Lead>{LEAD[kind]}</Lead>
      </div>
      {org ? <RuntimeAgentForm key={kind} org={org} kind={kind} onDone={(agent) => router.push(`/agents/${agent.slug}`)} /> : <Empty>Reading your session…</Empty>}
    </div>
  );
}
