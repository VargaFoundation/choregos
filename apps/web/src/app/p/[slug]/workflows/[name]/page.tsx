// SPDX-License-Identifier: Apache-2.0
"use client";

import { useState } from "react";
import { ActorIcon, Button, Card, Empty, ErrorNote, StateBadge } from "@/components/ui";
import { useBrouillon } from "@/components/workflows/brouillon";
import { PanneauDeTransition } from "@/components/workflows/panneaux";

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
export default function ProcessPage() {
  const brouillon = useBrouillon();
  const [ouverte, setOuverte] = useState<string | null>(null);
  if (brouillon.definition.error)
    return <ErrorNote>{String(brouillon.definition.error)}</ErrorNote>;
  // Le brouillon : les gestes pas encore publiés se lisent ici aussi (S16-12).
  const etapes = brouillon.process;
  const acteurs = Object.keys(brouillon.definition.data?.json?.actors ?? {});
  if (!etapes) return <Empty>reading the workflow…</Empty>;
  if (etapes.length === 0)
    return <Empty title="no transition">this workflow has no step yet.</Empty>;
  return (
    <ol className="space-y-3" aria-label="steps of the process">
      {etapes.map((etape, index) => (
        <li key={etape.id}>
          <Card>
            <div className="space-y-3" data-testid={`process-step-${etape.id}`}>
              <div className="flex items-start gap-2">
                <p className="flex-1 text-sm">
                  <span className="mr-2 text-ink-muted">{index + 1}.</span>
                  {etape.sentence}
                </p>
                <Button size="sm" onClick={() => setOuverte(ouverte === etape.id ? null : etape.id)}>
                  {ouverte === etape.id ? "close" : `edit ${etape.id}`}
                </Button>
              </div>
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
              {ouverte === etape.id && (
                <PanneauDeTransition
                  arete={{
                    id: etape.id,
                    from: etape.from,
                    to: etape.to,
                    kind: "nominal",
                    actor: etape.actor,
                    gates: (etape.gates ?? []).map((g) => g.name ?? "").filter(Boolean),
                    timeout_hours: etape.timeout_hours,
                  }}
                  acteurs={acteurs}
                />
              )}
            </div>
          </Card>
        </li>
      ))}
    </ol>
  );
}
