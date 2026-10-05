// SPDX-License-Identifier: Apache-2.0
"use client";

import { use } from "react";
import { ActorIcon, Card, Empty, ErrorNote, StateBadge } from "@/components/ui";
import { useWorkflow } from "@/components/workflows/use-workflow";

const GENRE: Record<string, string> = {
  agent: "agent",
  human: "human",
  system: "system",
  release_train: "train",
};

/**
 * La vue processus : chaque transition dite en clair (S16-08) — qui fait passer un ticket d'un
 * état au suivant, à quelles conditions, et ce qui arrive quand ça rate. C'est ce qu'un métier lit ;
 * la carte en montre la forme.
 */
export default function ProcessPage({
  params,
}: {
  params: Promise<{ slug: string; name: string }>;
}) {
  const { slug, name } = use(params);
  const { definition, validation } = useWorkflow(
    slug,
    decodeURIComponent(name),
  );
  if (definition.error)
    return <ErrorNote>{String(definition.error)}</ErrorNote>;
  const etapes = validation.data?.process;
  if (!etapes) return <Empty>reading the workflow…</Empty>;
  if (etapes.length === 0)
    return <Empty title="no transition">this workflow has no step yet.</Empty>;
  return (
    <ol className="space-y-3" aria-label="steps of the process">
      {etapes.map((etape, index) => (
        <li key={etape.id}>
          <Card>
            <div className="space-y-3" data-testid={`process-step-${etape.id}`}>
              <p className="text-sm">
                <span className="mr-2 text-ink-muted">{index + 1}.</span>
                {etape.sentence}
              </p>
              <dl className="grid grid-cols-[auto,1fr] gap-x-4 gap-y-1.5 text-xs">
                <dt className="text-ink-muted">from → to</dt>
                <dd className="flex flex-wrap items-center gap-2">
                  <StateBadge state={etape.from} display={etape.from_display} />
                  <span aria-hidden>→</span>
                  <StateBadge state={etape.to} display={etape.to_display} />
                </dd>
                <dt className="text-ink-muted">who</dt>
                <dd className="flex items-center gap-2">
                  <ActorIcon
                    kind={GENRE[etape.actor_type] ?? "system"}
                    name={etape.actor}
                  />
                  <span className="text-ink-muted">{etape.who}</span>
                </dd>
                {etape.gates && etape.gates.length > 0 && (
                  <>
                    <dt className="text-ink-muted">only if</dt>
                    <dd>
                      <ul className="space-y-0.5">
                        {etape.gates.map((garantie) => (
                          <li key={garantie.name}>
                            <code className="text-ink">{garantie.name}</code>
                            {garantie.summary ? ` — ${garantie.summary}` : ""}
                          </li>
                        ))}
                      </ul>
                    </dd>
                  </>
                )}
                {etape.outputs && etape.outputs.length > 0 && (
                  <>
                    <dt className="text-ink-muted">produces</dt>
                    <dd>{etape.outputs.join(", ")}</dd>
                  </>
                )}
                {etape.on_fail && (
                  <>
                    <dt className="text-ink-muted">on failure</dt>
                    <dd>{etape.on_fail}</dd>
                  </>
                )}
                {etape.on_reject && (
                  <>
                    <dt className="text-ink-muted">on rejection</dt>
                    <dd>{etape.on_reject}</dd>
                  </>
                )}
                {etape.timeout_hours && (
                  <>
                    <dt className="text-ink-muted">at most</dt>
                    <dd>{etape.timeout_hours} h</dd>
                  </>
                )}
              </dl>
            </div>
          </Card>
        </li>
      ))}
    </ol>
  );
}
