// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import dynamic from "next/dynamic";
import { useState } from "react";
import { OperationsDuProjet } from "@/components/operations-du-projet";
import { SettingsSection, useSectionWrites } from "@/components/project-settings/section";
import { Button, Disclosure } from "@/components/ui";
import { api } from "@/lib/api";

const YamlEditor = dynamic(() => import("@/components/yaml-editor").then((m) => m.YamlEditor), {
  ssr: false,
  loading: () => <p className="text-sm text-ink-muted">Loading the editor…</p>,
});

/** A sentence about a policy, read from its JSON: what a person looks for before opening the YAML. */
export function resumeDeLaPolitique(json: unknown): string {
  const policy = (json ?? {}) as { budgets?: Record<string, unknown>; release_train?: Record<string, { mode?: string }> };
  const parts: string[] = [];
  const budget = policy.budgets?.per_ticket_usd;
  if (typeof budget === "number") parts.push(`budget $${budget} per ticket`);
  const trains = Object.entries(policy.release_train ?? {}).map(([env, value]) => `${env}: ${value?.mode ?? "direct"}`);
  if (trains.length > 0) parts.push(`release ${trains.join(", ")}`);
  return parts.join(" · ") || "defaults of the organisation";
}

/** What bounds the agents: the policy of the project, and what each connector operation may do. */
export function GuardrailsSection({ slug }: { slug: string }) {
  const policy = useQuery({ queryKey: ["policy", slug], queryFn: () => api.policy(slug) });
  const { faire, notice } = useSectionWrites(slug);
  return (
    <SettingsSection
      id="guardrails"
      title="Guardrails"
      lead="The limits the orchestrator applies to this project's agents, and who must approve what a connector can do."
    >
      {notice}
      {(policy.data?.clamps ?? []).length > 0 && (
        <div className="text-sm" data-testid="policy-clamps">
          <p>The organisation&apos;s ceiling brings this policy down; what runs is clamped:</p>
          <ul className="list-disc pl-5 text-xs">
            {(policy.data?.clamps ?? []).map((clamp) => (
              <li key={clamp}>{clamp}</li>
            ))}
          </ul>
        </div>
      )}
      <Disclosure id="policy" title="Policy" summary={resumeDeLaPolitique(policy.data?.json)}>
        <div className="p-4 pt-0">
          <PolicyEditor
            yaml={policy.data?.yaml ?? ""}
            onSave={(yaml) => faire(() => api.putPolicy(slug, yaml), "Policy saved", ["policy"])}
          />
        </div>
      </Disclosure>
      <OperationsDuProjet slug={slug} />
    </SettingsSection>
  );
}

function PolicyEditor({ yaml, onSave }: { yaml: string; onSave: (yaml: string) => Promise<void> }) {
  const [texte, setTexte] = useState("");
  const [seeded, setSeeded] = useState<string | null>(null);
  if (yaml && yaml !== seeded) {
    setSeeded(yaml);
    setTexte(yaml);
  }
  return (
    <div className="space-y-2">
      <YamlEditor label="Policy YAML" value={texte} onChange={setTexte} issues={[]} />
      <div className="flex justify-end">
        <Button tone="primary" onClick={() => void onSave(texte)} disabled={!texte || texte === yaml}>
          Save the policy
        </Button>
      </div>
      <p className="text-xs text-ink-muted">
        Budgets, attempts, scope, sandbox and release trains: the same policy the orchestrator applies. The server
        validates it on save.
      </p>
      {/* `approvals.*` et `review.require_human_for_risk` passent la validation mais ne gardent rien
          (ADR 0044) : l'écran ne les range plus parmi ce qui s'applique, il dit où déclarer l'humain. */}
      <p className="text-xs text-ink-muted" data-testid="politique-sans-gardes">
        <code>approvals</code> and <code>review.require_human_for_risk</code> are accepted but not enforced: no one
        is asked. To make a person approve or review, declare a human transition in the workflow — a human actor, or{" "}
        <code>train.approval</code>. A release train&apos;s departure is approved through <code>release_train.&lt;env&gt;.approval</code>.
      </p>
    </div>
  );
}
