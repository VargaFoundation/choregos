// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { SettingsSection } from "@/components/project-settings/section";
import { ProjectMemoryStore } from "@/components/memory-store";
import { Card } from "@/components/ui";
import { api } from "@/lib/api";
import { servedToProject } from "@/lib/knowledge";
import { useSession } from "@/lib/session";

/** Whether the project's memory is on, read from its policy (`memory.enabled`, on by default). */
export function memoryIsOn(policyJson: unknown): boolean {
  const memory = (policyJson as { memory?: { enabled?: boolean } } | null | undefined)?.memory;
  return memory?.enabled !== false;
}

/** What the project's runs read and remember. Both have their own pages: this section counts and points. */
export function KnowledgeMemorySection({ slug }: { slug: string }) {
  const { org } = useSession();
  const bases = useQuery({ queryKey: ["knowledge", org], queryFn: () => api.knowledgeBases(org) });
  const policy = useQuery({ queryKey: ["policy", slug], queryFn: () => api.policy(slug) });
  const served = (bases.data ?? []).filter((b) => servedToProject(b, slug));
  return (
    <SettingsSection
      id="knowledge-memory"
      title="Knowledge & memory"
      lead="The documents the runs read and what the project remembers between runs."
    >
      <div className="grid gap-4 md:grid-cols-2">
        <Card title="Knowledge">
          <p className="text-sm" data-testid="knowledge-count">
            {bases.data ? `${served.length} knowledge base${served.length === 1 ? "" : "s"} served to this project.` : "Reading…"}
          </p>
          <p className="mt-2 text-xs">
            <Link href={`/p/${slug}/knowledge`}>Open Knowledge</Link>
          </p>
        </Card>
        <Card title="Memory">
          <p className="text-sm" data-testid="memory-state">
            {policy.data ? (memoryIsOn(policy.data.json) ? "Memory is on: runs recall and propose notes." : "Memory is off in this project's policy.") : "Reading…"}
          </p>
          <ProjectMemoryStore slug={slug} />
          <p className="mt-2 text-xs">
            <Link href={`/p/${slug}/memory`}>Open Memory</Link>
            <span className="text-ink-muted"> · switched on or off in the guardrails policy (memory.enabled)</span>
          </p>
        </Card>
      </div>
    </SettingsSection>
  );
}
