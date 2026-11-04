// SPDX-License-Identifier: Apache-2.0
"use client";

import { useParams } from "next/navigation";
import { useState } from "react";
import { Button, Card, Empty, ErrorNote, EngineSentence, StateBadge } from "@/components/ui";
import { useDraft } from "@/components/workflows/draft";
import { StepForm, TransitionPanel } from "@/components/workflows/panels";
import { StepDetails } from "@/components/workflows/step-details";
import { decisionActorOf } from "@/components/workflows/document";
import { workflowThresholds } from "@/components/workflows/decision-calibration";
import { UnreadableText } from "@/components/workflows/unreadable-text";
import { useAgentResolution } from "@/components/workflows/use-agent-resolution";
import { timingOfStep } from "@/components/workflows/timing";

/**
 * The steps of a workflow (S16-08; the default view until S55-06, now one tab after the graph): each
 * transition said plainly — who moves a ticket from one state to the next, on what conditions, and what
 * happens when it fails. It is what a trade reads; the graph shows its shape.
 */
export default function StepsPage() {
  const draft = useDraft();
  const { slug, name } = useParams<{ slug: string; name: string }>();
  const resolutions = useAgentResolution(slug, draft.process);
  const [opened, setOpened] = useState<string | null>(null);
  if (draft.definition.error)
    return <ErrorNote>{String(draft.definition.error)}</ErrorNote>;
  // The draft: gestures not yet published read here too (S16-12).
  const steps = draft.process;
  const actorNames = Object.keys(draft.definition.data?.json?.actors ?? {});
  const definitions = draft.definition.data?.json?.actors as
    | Record<string, { type?: string; group?: string; role?: string; agent?: string }>
    | undefined;
  const byId = new Map((steps ?? []).map((e) => [e.id, e]));
  if (!steps) return <Empty>Reading the workflow…</Empty>;
  const addStep = (
    <Card title="Add a step">
      <StepForm
        states={(draft.graph?.nodes ?? []) as { id: string; display: string; terminal?: boolean }[]}
        actors={actorNames}
      />
    </Card>
  );
  if (steps.length === 0)
    return (
      <div className="space-y-3">
        <Empty title="No transition">This workflow has no step yet.</Empty>
        {addStep}
      </div>
    );
  return (
    <div className="space-y-3">
      {draft.graphStale && <UnreadableText />}
      <ol className={draft.graphStale ? "space-y-3 opacity-60" : "space-y-3"} aria-label="Steps of the process">
        {steps.map((step, index) => (
          <li key={step.id}>
            <Card>
              <div className="space-y-3" data-testid={`process-step-${step.id}`}>
                <div className="flex items-start gap-2">
                  <p className="min-w-0 flex-1 break-words text-sm">
                    <span className="mr-2 text-ink-muted">{index + 1}.</span>
                    <EngineSentence text={step.sentence} />
                  </p>
                  <Button size="sm" onClick={() => setOpened(opened === step.id ? null : step.id)}>
                    {opened === step.id ? "Close" : `Edit ${step.id}`}
                  </Button>
                </div>
                <StepDetails
                  step={step}
                  resolution={step.agent ? resolutions.get(step.agent) : undefined}
                  flow={draft.graph?.artifacts}
                  stepsById={byId}
                  decision={decisionActorOf(draft.document, step.actor)}
                  calibration={{ project: slug, workflow: name, thresholds: workflowThresholds(draft.document, step.to) }}
                />
                <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-xs">
                  <dt className="text-ink-muted">From → to</dt>
                  <dd className="flex flex-wrap items-center gap-2">
                    <StateBadge state={step.from} display={step.from_display} />
                    <span aria-hidden>→</span>
                    <StateBadge state={step.to} display={step.to_display} />
                  </dd>
                  {step.gates && step.gates.length > 0 && (
                    <>
                      <dt className="text-ink-muted">Only if</dt>
                      <dd>
                        <ul className="space-y-0.5">
                          {step.gates.map((gate) => (
                            <li key={gate.name}>
                              <code className="text-ink">{gate.name}</code>
                              {gate.summary ? ` — ${gate.summary}` : ""}
                            </li>
                          ))}
                        </ul>
                      </dd>
                    </>
                  )}
                  {step.on_fail && (
                    <>
                      <dt className="text-ink-muted">On failure</dt>
                      <dd>{step.on_fail}</dd>
                    </>
                  )}
                  {step.on_reject && (
                    <>
                      <dt className="text-ink-muted">On rejection</dt>
                      <dd>{step.on_reject}</dd>
                    </>
                  )}
                  {step.timeout_hours && (
                    <>
                      <dt className="text-ink-muted">At most</dt>
                      <dd>{step.timeout_hours} h</dd>
                    </>
                  )}
                </dl>
                {opened === step.id && (
                  <TransitionPanel
                    edge={{
                      id: step.id,
                      from: step.from,
                      to: step.to,
                      kind: "nominal",
                      actor: step.actor,
                      gates: (step.gates ?? []).map((g) => g.name ?? "").filter(Boolean),
                      timeout_hours: step.timeout_hours,
                    }}
                    actors={actorNames}
                    definitions={definitions}
                    gates={step.gates}
                    timing={timingOfStep(step, {
                      timeoutHours: step.timeout_hours,
                      graph: draft.graph,
                      onTimeout: (draft.definition.data?.json as { defaults?: { on_timeout?: string } } | undefined)
                        ?.defaults?.on_timeout,
                    })}
                  />
                )}
              </div>
            </Card>
          </li>
        ))}
      </ol>
      {addStep}
    </div>
  );
}
