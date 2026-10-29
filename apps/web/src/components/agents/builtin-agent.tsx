// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { Badge, Disclosure, LEGENDE } from "@/components/ui";
import { api, type BuiltinAgentQuery } from "@/lib/api";
import { cn } from "@/lib/cn";
import type { BuiltinAgent } from "@/lib/types";

type Choice = NonNullable<BuiltinAgent["models"]>[number];

/** What a run of an `agent` actor that names no registry agent loads, as the API says (absent while it loads). */
export function useBuiltinAgent(slug: string, actor: BuiltinAgentQuery | null) {
  return useQuery({
    queryKey: ["builtin-agent", slug, actor],
    queryFn: () => api.describeBuiltinAgent(slug, actor!),
    enabled: actor != null,
    retry: false,
    staleTime: 60_000,
  });
}

/** The sizes that resolve to the same model, together: `S, M → standard`. */
export function groupBySize(models: Choice[]): { sizes: string[]; choice: Choice }[] {
  const groups: { sizes: string[]; choice: Choice }[] = [];
  const key = (c: Choice) => JSON.stringify([c.profile, c.litellm_model, c.provider_model, c.turns_factor, c.error]);
  for (const choice of models) {
    const last = groups.at(-1);
    if (last && key(last.choice) === key(choice)) last.sizes.push(choice.size ?? "");
    else groups.push({ sizes: choice.size ? [choice.size] : [], choice });
  }
  return groups;
}

function Model({ sizes, choice }: { sizes: string[]; choice: Choice }) {
  const behind = choice.provider_model && choice.provider_model !== choice.litellm_model ? choice.provider_model : null;
  return (
    <li>
      {sizes.length > 0 && <span className="text-ink-muted">size {sizes.join(", ")} → </span>}
      {choice.error ? (
        <span className="text-failed-ink">refused: {choice.error}</span>
      ) : (
        <>
          {choice.profile && choice.profile !== choice.litellm_model && <span className="text-ink-muted">{choice.profile}: </span>}
          <code className="text-ink">{behind ?? choice.litellm_model}</code>
          {behind && <span className="text-ink-muted"> (alias {choice.litellm_model})</span>}
          {(choice.turns_factor ?? 1) !== 1 && <span className="text-ink-muted"> · turns ×{choice.turns_factor}</span>}
        </>
      )}
    </li>
  );
}

/**
 * What a built-in agent is (a workflow `agent` actor that names no registry agent): the playbook its
 * role reads — the prompt, as a run receives it —, the model each size resolves to, the backend, the
 * limits, the memory and the MCP servers with their tools. Read only: to change any of it, register
 * the agent and name it in the workflow.
 */
export function BuiltinAgentDetails({
  agent,
  projectSlug,
  className,
}: {
  agent: BuiltinAgent;
  /** Links to the project's agents page, where the agent is registered; absent on that page itself. */
  projectSlug?: string;
  className?: string;
}) {
  const models = groupBySize(agent.models ?? []);
  return (
    <div className={cn("space-y-3", className)} data-testid="builtin-agent">
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-xs">
        <dt className={LEGENDE}>Kind</dt>
        <dd className="flex flex-wrap items-center gap-2">
          <Badge tone="agent" variant="soft" dot={false}>
            built-in agent
          </Badge>
          <span className="text-ink-muted">the platform&apos;s {agent.role} playbook, not in the registry</span>
        </dd>
        <dt className={LEGENDE}>Model</dt>
        <dd>
          <span className="text-ink-muted">
            <code>{agent.model}</code>
          </span>
          <ul className="space-y-0.5" data-testid="builtin-agent-models">
            {models.map((group) => (
              <Model key={group.sizes.join(",") || "any"} sizes={group.sizes} choice={group.choice} />
            ))}
          </ul>
        </dd>
        <dt className={LEGENDE}>Runs on</dt>
        <dd>
          <code className="text-ink">{agent.backend}</code>
          <span className="text-ink-muted">{agent.backend_source === "project" ? " — the project's default backend" : " — set on the actor"}</span>
        </dd>
        <dt className={LEGENDE}>Limits</dt>
        <dd>
          {agent.max_turns} turns · {agent.max_minutes} min
          <span className="text-ink-muted">{agent.limits_source === "policy" ? " — the project policy's, for this role" : " — set on the actor"}</span>
        </dd>
        {(agent.memory ?? []).length > 0 && (
          <>
            <dt className={LEGENDE}>Memory</dt>
            <dd>{(agent.memory ?? []).join(", ")}</dd>
          </>
        )}
        <dt className={LEGENDE}>Tools</dt>
        <dd>
          <ul className="space-y-1" data-testid="builtin-agent-tools">
            {(agent.mcp_servers ?? []).map((server) => (
              <li key={server.name}>
                <span className="font-medium text-ink">MCP {server.name}</span>
                <span className="text-ink-muted"> — {server.description}</span>
                {(server.tools ?? []).length > 0 && (
                  <div className="mt-0.5 flex flex-wrap gap-1">
                    {(server.tools ?? []).map((tool) => (
                      <code key={tool} className="rounded bg-surface-sunken px-1">
                        {tool}
                      </code>
                    ))}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </dd>
      </dl>
      {agent.instructions ? (
        <Disclosure
          title="Prompt"
          summary={`the ${agent.playbook} playbook${agent.playbook_origin === "deployment" ? ", supplied by the deployment" : ""}`}
        >
          <pre className="max-h-96 overflow-auto whitespace-pre-wrap font-mono text-xs" data-testid="builtin-agent-prompt">
            {agent.instructions}
          </pre>
          <p className="text-xs text-ink-muted">
            The parts in &lt;angle brackets&gt; are filled in by each run, from the work item and its documents.
          </p>
        </Disclosure>
      ) : (
        <p className="text-xs text-failed-ink">{agent.instructions_error ?? "no prompt"}</p>
      )}
      <p className="text-xs text-ink-muted">
        No editable instructions, versions or costs of its own.{" "}
        {projectSlug ? (
          <>
            <Link href={`/p/${projectSlug}/agents`} className="underline">
              Register it
            </Link>{" "}
            and name it in the workflow (<code>agent: its-slug</code>) to change them.
          </>
        ) : (
          <>Register it, then name it in the workflow (<code>agent: its-slug</code>) to change them.</>
        )}
      </p>
    </div>
  );
}

/** A workflow actor's own fields, as the definition (or the draft) writes them. */
export type AgentActorFields = {
  role?: string;
  playbook?: string;
  model?: string;
  backend?: string;
  max_turns?: number;
  max_minutes?: number;
};

/** The built-in agent of an `agent` actor: read from the API with the actor's own fields, so a draft is described too. */
export function BuiltinAgentOfActor({
  projectSlug,
  actor,
  linkToAgents = true,
}: {
  projectSlug: string;
  actor: AgentActorFields;
  linkToAgents?: boolean;
}) {
  const lu = useBuiltinAgent(
    projectSlug,
    actor.role
      ? {
          role: actor.role,
          playbook: actor.playbook,
          model: actor.model,
          backend: actor.backend,
          max_turns: actor.max_turns,
          max_minutes: actor.max_minutes,
        }
      : null,
  );
  if (!actor.role) return null;
  if (lu.error) return <p className="text-xs text-ink-muted">What this agent runs could not be read: {String(lu.error)}</p>;
  if (!lu.data) return <p className="text-xs text-ink-muted">Reading what this agent runs…</p>;
  return <BuiltinAgentDetails agent={lu.data} projectSlug={linkToAgents ? projectSlug : undefined} />;
}
