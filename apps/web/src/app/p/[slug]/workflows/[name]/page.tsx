// SPDX-License-Identifier: Apache-2.0
"use client";

import { useParams } from "next/navigation";
import { useState } from "react";
import { Button, Card, Empty, ErrorNote, PhraseDuMoteur, StateBadge } from "@/components/ui";
import { useBrouillon } from "@/components/workflows/brouillon";
import { FormulaireDEtape, PanneauDeTransition } from "@/components/workflows/panneaux";
import { StepDetails } from "@/components/workflows/step-details";
import { TexteIllisible } from "@/components/workflows/texte-illisible";
import { useAgentResolution } from "@/components/workflows/use-agent-resolution";

/**
 * La vue processus : chaque transition dite en clair (S16-08) — qui fait passer un ticket d'un
 * état au suivant, à quelles conditions, et ce qui arrive quand ça rate. C'est ce qu'un métier lit ;
 * la carte en montre la forme.
 */
export default function ProcessPage() {
  const brouillon = useBrouillon();
  const { slug } = useParams<{ slug: string }>();
  const resolutions = useAgentResolution(slug, brouillon.process);
  const [ouverte, setOuverte] = useState<string | null>(null);
  if (brouillon.definition.error)
    return <ErrorNote>{String(brouillon.definition.error)}</ErrorNote>;
  // Le brouillon : les gestes pas encore publiés se lisent ici aussi (S16-12).
  const etapes = brouillon.process;
  const acteurs = Object.keys(brouillon.definition.data?.json?.actors ?? {});
  const parId = new Map((etapes ?? []).map((e) => [e.id, e]));
  if (!etapes) return <Empty>Reading the workflow…</Empty>;
  const ajouter = (
    <Card title="Add a step">
      <FormulaireDEtape
        etats={(brouillon.graph?.nodes ?? []) as { id: string; display: string; terminal?: boolean }[]}
        acteurs={acteurs}
      />
    </Card>
  );
  if (etapes.length === 0)
    return (
      <div className="space-y-3">
        <Empty title="No transition">This workflow has no step yet.</Empty>
        {ajouter}
      </div>
    );
  return (
    <div className="space-y-3">
      {brouillon.grapheObsolete && <TexteIllisible />}
      <ol className={brouillon.grapheObsolete ? "space-y-3 opacity-60" : "space-y-3"} aria-label="Steps of the process">
        {etapes.map((etape, index) => (
          <li key={etape.id}>
            <Card>
              <div className="space-y-3" data-testid={`process-step-${etape.id}`}>
                <div className="flex items-start gap-2">
                  <p className="min-w-0 flex-1 break-words text-sm">
                    <span className="mr-2 text-ink-muted">{index + 1}.</span>
                    <PhraseDuMoteur texte={etape.sentence} />
                  </p>
                  <Button size="sm" onClick={() => setOuverte(ouverte === etape.id ? null : etape.id)}>
                    {ouverte === etape.id ? "Close" : `Edit ${etape.id}`}
                  </Button>
                </div>
                <StepDetails
                  step={etape}
                  resolution={etape.agent ? resolutions.get(etape.agent) : undefined}
                  flow={brouillon.graph?.artifacts}
                  stepsById={parId}
                />
                <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-xs">
                  <dt className="text-ink-muted">From → to</dt>
                  <dd className="flex flex-wrap items-center gap-2">
                    <StateBadge state={etape.from} display={etape.from_display} />
                    <span aria-hidden>→</span>
                    <StateBadge state={etape.to} display={etape.to_display} />
                  </dd>
                  {etape.gates && etape.gates.length > 0 && (
                    <>
                      <dt className="text-ink-muted">Only if</dt>
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
                  {etape.on_fail && (
                    <>
                      <dt className="text-ink-muted">On failure</dt>
                      <dd>{etape.on_fail}</dd>
                    </>
                  )}
                  {etape.on_reject && (
                    <>
                      <dt className="text-ink-muted">On rejection</dt>
                      <dd>{etape.on_reject}</dd>
                    </>
                  )}
                  {etape.timeout_hours && (
                    <>
                      <dt className="text-ink-muted">At most</dt>
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
      {ajouter}
    </div>
  );
}
