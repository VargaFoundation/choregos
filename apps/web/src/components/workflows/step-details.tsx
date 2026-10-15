// SPDX-License-Identifier: Apache-2.0
import Link from "next/link";
import { ActorIcon, Badge, LEGENDE, PhraseDuMoteur } from "@/components/ui";
import { enClair } from "@/components/parcours/modele";
import { cn } from "@/lib/cn";
import type { AgentResolution, ArtifactFlow, ProcessStep } from "@/lib/types";

/** The actor kinds the process view returns, as `ActorIcon` names them. */
export const GENRE_DE_L_ACTEUR: Record<string, string> = {
  agent: "agent",
  human: "human",
  system: "system",
  job: "job",
  release_train: "train",
};

/** The status of the registry agent a step names, as one chip: what a run would load, or why not. */
function Statut({ resolution }: { resolution: AgentResolution | undefined }) {
  if (!resolution) return null;
  if (resolution.status === "resolved")
    return (
      <Badge tone="succeeded" variant="soft" dot={false}>
        v{resolution.version} · {resolution.pinned ? "pinned" : "latest"}
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

/** Where a document an input reads comes from, in words. */
function Source({ nom, flow, parId }: { nom: string; flow: ArtifactFlow | undefined; parId: Map<string, ProcessStep> }) {
  if (!flow) return null;
  if (flow.origin === "feedback") return <span>a person&apos;s rejection reason</span>;
  if (flow.origin === "field") return <span>a work item field</span>;
  if (flow.origin === "missing")
    return (
      <span className="text-retrying-ink" data-testid={`manque-${nom}`}>
        nothing produces it
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
export function StepDetails({
  step,
  resolution,
  flow,
  stepsById,
  className,
}: {
  step: ProcessStep;
  /** The answer for `step.agent`; absent while it loads or against an API without the endpoint. */
  resolution?: AgentResolution;
  flow?: ArtifactFlow[];
  stepsById: Map<string, ProcessStep>;
  className?: string;
}) {
  const parNom = new Map((flow ?? []).map((a) => [a.name, a]));
  const lecture = step.inputs ?? [];
  const sorties = step.outputs ?? [];
  const types = step.output_types ?? {};
  const lecteurs = (nom: string) => (parNom.get(nom)?.read_by ?? []).filter((id) => id !== step.id);
  const slug = step.agent?.split("@")[0];
  return (
    <dl className={cn("grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-xs", className)} data-testid={`details-${step.id}`}>
      <dt className={LEGENDE}>Who</dt>
      <dd className="flex flex-wrap items-center gap-x-2 gap-y-1">
        {step.actor_type === "agent" && slug ? (
          <>
            <Link href={`/agents/${slug}`} className="font-medium text-ink underline-offset-2 hover:underline">
              {resolution?.display_name ?? enClair(step.actor)}
            </Link>
            <Statut resolution={resolution} />
          </>
        ) : step.actor_type === "agent" ? (
          <>
            <ActorIcon kind="agent" name={enClair(step.actor)} />
            <span className="text-ink-muted">built-in playbook, not a registry agent</span>
          </>
        ) : step.actor_type === "human" ? (
          <>
            <ActorIcon kind="human" name={step.group ?? enClair(step.actor)} />
            {step.sla_hours ? <Badge tone="neutral" variant="soft" dot={false}>{step.sla_hours} h</Badge> : null}
          </>
        ) : step.actor_type === "job" ? (
          <>
            <ActorIcon kind="job" name={enClair(step.actor)} />
            <code className="font-mono">{(step.command ?? []).join(" ")}</code>
            {step.image ? <span className="text-ink-muted">in {step.image}</span> : null}
          </>
        ) : (
          <>
            <ActorIcon kind={GENRE_DE_L_ACTEUR[step.actor_type] ?? "system"} />
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
      {lecture.length > 0 && (
        <>
          <dt className={LEGENDE}>Reads</dt>
          <dd>
            <ul className="space-y-0.5">
              {lecture.map((nom) => (
                <li key={nom}>
                  <code className="text-ink">{nom}</code>
                  {parNom.has(nom) ? (
                    <>
                      {" — "}
                      <span className="text-ink-muted">
                        <Source nom={nom} flow={parNom.get(nom)} parId={stepsById} />
                      </span>
                    </>
                  ) : null}
                </li>
              ))}
            </ul>
          </dd>
        </>
      )}
      {(sorties.length > 0 || step.writes_branch) && (
        <>
          <dt className={LEGENDE}>Produces</dt>
          <dd>
            <ul className="space-y-0.5">
              {sorties.map((nom) => (
                <li key={nom}>
                  <code className="text-ink">{nom}</code>
                  {types[nom] ? (
                    <Badge tone="neutral" variant="soft" dot={false} className="ml-1.5">
                      {types[nom]}
                    </Badge>
                  ) : null}
                  {lecteurs(nom).length > 0 && (
                    <span className="text-ink-muted"> — read by {lecteurs(nom).join(", ")}</span>
                  )}
                </li>
              ))}
              {step.writes_branch && <li>changes on the work item&apos;s branch</li>}
            </ul>
          </dd>
        </>
      )}
    </dl>
  );
}
