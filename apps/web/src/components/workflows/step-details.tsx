// SPDX-License-Identifier: Apache-2.0
import Link from "next/link";
import { ActorIcon, Badge, LEGENDE, PhraseDuMoteur } from "@/components/ui";
import { canarySentence, sourceLabel } from "@/components/agents/registre";
import { humanise } from "@/components/journey/model";
import { cn } from "@/lib/cn";
import type { AgentResolution, ArtifactFlow, ProcessStep } from "@/lib/types";
import { answerWords, DecisionChip, exampleWhen, questionKind } from "@/components/workflows/decision";
import {
  DecisionCalibration,
  ProbabilityNote,
  useStepCalibration,
  type CalibrationRead,
  type CalibrationSource,
} from "@/components/workflows/decision-calibration";
import {
  CONFIDENCE_SUFFIX,
  DEFAULT_DECISION_MODEL,
  DEFAULT_DECISION_TIMEOUT_SECONDS,
  decisionOutputs,
  type WorkflowDecisionActor,
} from "@/components/workflows/document";

/** The actor kinds the process view returns, as `ActorIcon` names them. */
export const ACTOR_KIND_ICON: Record<string, string> = {
  agent: "agent",
  human: "human",
  system: "system",
  job: "job",
  decision: "decision",
  release_train: "train",
};

/** The status of the registry agent a step names, as one chip: what a run would load, or why not. */
function ResolutionStatus({ resolution }: { resolution: AgentResolution | undefined }) {
  if (!resolution) return null;
  if (resolution.status === "resolved")
    return (
      <Badge tone="succeeded" variant="soft" dot={false}>
        v{resolution.version} · {sourceLabel(resolution)}
        {canarySentence(resolution) ? ` — ${canarySentence(resolution)}` : ""}
      </Badge>
    );
  if (resolution.status === "not_installed")
    return resolution.in_catalogue ? (
      <Badge tone="retrying" variant="soft">
        not installed: installs when this workflow is published
      </Badge>
    ) : (
      <Badge tone="failed" variant="soft">
        unknown agent
      </Badge>
    );
  return (
    <Badge tone="failed" variant="soft">
      unavailable: {resolution.reason ?? resolution.lifecycle ?? "a run would not start"}
    </Badge>
  );
}

/**
 * What a step gets when nothing in the workflow produces what it reads (the runtime's own fallbacks):
 * an input is read empty, and a run with no allowed paths may write anywhere — the stage gives it `**`
 * unless the work item was filed with paths of its own.
 */
export function consequenceOfAMissingInput(name: string): string {
  if (name === "allowed_paths")
    return "unless the work item was filed with allowed paths, the agent may change any file of the repository (`**`), and `scope_respected` has nothing to hold it to";
  return "the step reads it empty";
}

/** Where a document an input reads comes from, in words. */
function Source({ name, flow, parId }: { name: string; flow: ArtifactFlow | undefined; parId: Map<string, ProcessStep> }) {
  if (!flow) return null;
  if (flow.origin === "feedback") return <span>a person&apos;s rejection reason</span>;
  if (flow.origin === "field") return <span>a work item field</span>;
  if (flow.origin === "missing")
    return (
      <span className="text-retrying-ink" data-testid={`manque-${name}`}>
        nothing produces it — {consequenceOfAMissingInput(name)}
      </span>
    );
  return (
    <span>
      from{" "}
      {flow.produced_by.map((id, index) => (
        <span key={id}>
          {index > 0 && ", "}
          <em className="not-italic text-ink">{parId.get(id)?.to_display ?? id}</em> (<code>{id}</code>)
        </span>
      ))}
    </span>
  );
}

/**
 * What one step of a workflow says about itself (S39-13): who carries it — the registry agent and the
 * version a run would load, a group and its SLA, a job's command —, what it reads and from where, what
 * it produces. Read only: the editor panel stays apart, under it.
 */
type StepDetailsProps = {
  step: ProcessStep;
  /** The decision actor the step names (ADR 0121), read from the workflow: its questions, state and model. */
  decision?: WorkflowDecisionActor;
  /** Where a decision step's calibration is read (S52-06b); without it, the card says nothing is measured. */
  calibration?: CalibrationSource;
  /** The answer for `step.agent`; absent while it loads or against an API without the endpoint. */
  resolution?: AgentResolution;
  flow?: ArtifactFlow[];
  stepsById: Map<string, ProcessStep>;
  className?: string;
};

export function StepDetails(props: StepDetailsProps) {
  // Only a decision step reads its calibration, once, for both the note on its probability and its row.
  if (props.decision && props.calibration) return <DecisionStepDetails {...props} calibration={props.calibration} />;
  return <StepDetailsCard {...props} />;
}

function DecisionStepDetails(props: StepDetailsProps & { calibration: CalibrationSource }) {
  const read = useStepCalibration(props.calibration.project, props.calibration.workflow, props.step.from);
  return <StepDetailsCard {...props} read={read} />;
}

function StepDetailsCard({
  step,
  resolution,
  flow,
  stepsById,
  decision,
  calibration,
  read,
  className,
}: StepDetailsProps & { read?: CalibrationRead }) {
  const byName = new Map((flow ?? []).map((a) => [a.name, a]));
  const reads = step.inputs ?? [];
  // A decision writes each answer and its probability; an older API listed none for it.
  const outputs = step.outputs?.length ? step.outputs : decision ? decisionOutputs(decision) : [];
  const questions = decision?.questions ?? {};
  // What a `when` reads in a decision's output: the answer and what it may be, or its probability.
  const noteOnAnOutput = (name: string): string | null => {
    const question = questions[name];
    if (question) return `the answer, ${answerWords(question)}`;
    if (name.endsWith(CONFIDENCE_SUFFIX) && questions[name.slice(0, -CONFIDENCE_SUFFIX.length)]) return "its probability, from 0 to 1";
    return null;
  };
  const types = step.output_types ?? {};
  const readers = (name: string) => (byName.get(name)?.read_by ?? []).filter((id) => id !== step.id);
  const slug = step.agent?.split("@")[0];
  return (
    <dl className={cn("grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-xs", className)} data-testid={`details-${step.id}`}>
      <dt className={LEGENDE}>Who</dt>
      <dd className="flex flex-wrap items-center gap-x-2 gap-y-1">
        {step.actor_type === "agent" && slug ? (
          <>
            <Link href={`/agents/${slug}`} className="font-medium text-ink underline-offset-2 hover:underline">
              {resolution?.display_name ?? humanise(step.actor)}
            </Link>
            <ResolutionStatus resolution={resolution} />
          </>
        ) : step.actor_type === "agent" ? (
          <>
            <ActorIcon kind="agent" name={humanise(step.actor)} />
            <span className="text-ink-muted">built-in agent, not in the registry</span>
          </>
        ) : step.actor_type === "human" ? (
          <>
            <ActorIcon kind="human" name={step.group ?? humanise(step.actor)} />
            {step.sla_hours ? <Badge tone="neutral" variant="soft" dot={false}>{step.sla_hours} h</Badge> : null}
          </>
        ) : step.actor_type === "decision" ? (
          <>
            <DecisionChip />
            <span className="font-medium text-ink">{humanise(step.actor)}</span>
            {step.role ? <span className="text-ink-muted">role {step.role}</span> : null}
          </>
        ) : step.actor_type === "job" ? (
          <>
            <ActorIcon kind="job" name={humanise(step.actor)} />
            <code className="font-mono">{(step.command ?? []).join(" ")}</code>
            {step.image ? <span className="text-ink-muted">in {step.image}</span> : null}
          </>
        ) : (
          <>
            <ActorIcon kind={ACTOR_KIND_ICON[step.actor_type] ?? "system"} />
            <span className="text-ink-muted">
              <PhraseDuMoteur texte={step.who} />
            </span>
          </>
        )}
        {step.actor_type === "agent" && (step.role || step.model) && (
          <span className="text-ink-muted">
            {[step.role && `role ${step.role}`, step.model].filter(Boolean).join(" · ")}
          </span>
        )}
      </dd>
      {step.actor_type === "decision" && (
        <>
          <dt className={LEGENDE}>Model</dt>
          <dd>
            <code className="text-ink">{decision?.model ?? step.model ?? DEFAULT_DECISION_MODEL}</code>
            <span className="text-ink-muted">
              {" "}
              — answers within {decision?.timeout_seconds ?? DEFAULT_DECISION_TIMEOUT_SECONDS} s, through the project&apos;s gateway
            </span>
          </dd>
        </>
      )}
      {decision && (
        <>
          <dt className={LEGENDE}>Sends</dt>
          <dd data-testid={`decision-state-${step.id}`}>
            <span className="flex flex-wrap gap-1">
              {decision.state.map((source) => (
                <code key={source} className="rounded border border-line px-1 text-ink">
                  {source}
                </code>
              ))}
            </span>
            <span className="text-ink-muted">nothing else reaches the model: not the repository, not the memory</span>
          </dd>
          <dt className={LEGENDE}>Asks</dt>
          <dd>
            <ul className="space-y-0.5" data-testid={`decision-questions-${step.id}`}>
              {Object.entries(decision.questions).map(([name, question]) => (
                <li key={name}>
                  <code className="text-ink">{name}</code> <span className="text-ink-muted">({questionKind(question)}: {answerWords(question)})</span>
                  {" — "}
                  {question.instructions}
                </li>
              ))}
            </ul>
          </dd>
        </>
      )}
      {reads.length > 0 && (
        <>
          <dt className={LEGENDE}>Reads</dt>
          <dd>
            <ul className="space-y-0.5">
              {reads.map((name) => (
                <li key={name}>
                  <code className="text-ink">{name}</code>
                  {byName.has(name) ? (
                    <>
                      {" — "}
                      <span className="text-ink-muted">
                        <Source name={name} flow={byName.get(name)} parId={stepsById} />
                      </span>
                    </>
                  ) : null}
                </li>
              ))}
            </ul>
          </dd>
        </>
      )}
      {(outputs.length > 0 || step.writes_branch) && (
        <>
          <dt className={LEGENDE}>Produces</dt>
          <dd>
            <ul className="space-y-0.5">
              {outputs.map((name) => (
                <li key={name}>
                  <code className="text-ink">{name}</code>
                  {types[name] ? (
                    <Badge tone="neutral" variant="soft" dot={false} className="ml-1.5">
                      {types[name]}
                    </Badge>
                  ) : null}
                  {noteOnAnOutput(name) ? <span className="text-ink-muted"> — {noteOnAnOutput(name)}</span> : null}
                  {readers(name).length > 0 && (
                    <span className="text-ink-muted"> — read by {readers(name).join(", ")}</span>
                  )}
                </li>
              ))}
              {step.writes_branch && <li>changes on the work item&apos;s branch</li>}
            </ul>
            {decision && exampleWhen(decision) && (
              <p className="mt-1 text-ink-muted" data-testid={`decision-when-${step.id}`}>
                A transition from the next state compares them: <code className="text-ink">{exampleWhen(decision)}</code>.{" "}
                <ProbabilityNote calibration={read?.data} />
              </p>
            )}
          </dd>
        </>
      )}
      {decision && read && (
        <>
          <dt className={LEGENDE}>Calibration</dt>
          <dd data-testid={`decision-calibration-${step.id}`}>
            <DecisionCalibration read={read} thresholds={calibration?.thresholds} />
          </dd>
        </>
      )}
    </dl>
  );
}
