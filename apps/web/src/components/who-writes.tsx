// SPDX-License-Identifier: Apache-2.0
"use client";

/**
 * Who writes, and with which account (S42-06g, ADR 0107 story 1): on a tracker connector's card, on a
 * work item, on a run. The bookkeeping goes through the project's tracker connector; an agent's own
 * actions go through its MCP tools, called by the broker with each connector's credential — never
 * from the agent's pod.
 */
import Link from "next/link";
import { Card } from "@/components/ui";
import { Provenance } from "@/components/provenance";
import { orgConnectorHref, projectConnectionsHref } from "@/lib/connector-link";
import type { ConnectorDto, Run, RunChain } from "@/lib/types";
import { initiatorInWords, toolAccount, trackerAccount, trackerName } from "@/lib/who-writes";

/** The line a tracker connector's card carries: the account its writes go out under. */
export function WritesAs({ type, config }: { type: string; config?: Record<string, unknown> }) {
  const account = trackerAccount(type, config);
  if (!account) return null;
  return (
    <p className="text-xs text-ink-muted" data-testid="writes-as">
      Choregos writes as <span className="text-ink">{account}</span>
    </p>
  );
}

/** A work item's tracker: where it lives, who started it, who writes to it and how. */
export function TrackerCard({
  slug,
  tracker,
  initiatedBy,
  url,
}: {
  slug: string;
  tracker?: ConnectorDto;
  initiatedBy?: string | null;
  url?: string | null;
}) {
  const account = tracker ? trackerAccount(tracker.type, tracker.config) : null;
  const startedBy = initiatorInWords(initiatedBy);
  return (
    <Card title="Tracker" action={<Provenance de="observed" />}>
      <div className="space-y-2 text-sm" data-testid="tracker-card">
        {url && (
          <p>
            <a href={url} target="_blank" rel="noreferrer">
              Open in {tracker ? trackerName(tracker.type) : "the tracker"}
            </a>
          </p>
        )}
        {startedBy && (
          <p className="text-ink-muted">
            Started by{" "}
            <span className="text-ink" data-donnee>
              {startedBy}
            </span>
          </p>
        )}
        {account && tracker ? (
          <p className="text-ink-muted">
            Choregos writes to {trackerName(tracker.type)} as <span className="text-ink">{account}</span>, through the
            project&apos;s <Link href={projectConnectionsHref(slug)}>tracker connector</Link>: the state, the status
            comment, the Cost, Size, Risk and Run fields, the questions. An agent&apos;s own actions go through its MCP
            tools, under each tool&apos;s account: see its runs.
          </p>
        ) : (
          <p className="text-ink-muted">
            This work item lives in the internal tracker: Choregos writes to no external tracker.
          </p>
        )}
      </div>
    </Card>
  );
}

/** A run's identity: its agent version, who started it, the chain, and the tools its agent may call. */
export function RunIdentity({ run, chain }: { run: Run; chain?: RunChain }) {
  const startedBy = initiatorInWords(chain?.initiated_by ?? run.initiated_by);
  const tools = run.tool_connectors ?? [];
  return (
    <Card title="Identity and tools" action={<Provenance de="observed" />}>
      <div className="space-y-3 text-sm" data-testid="run-identity">
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
          <dt className="text-ink-muted">Agent</dt>
          <dd>{run.agent_slug ? `${run.agent_slug} v${run.agent_version ?? "?"}` : "none from the registry"}</dd>
          <dt className="text-ink-muted">Started by</dt>
          <dd data-donnee>{startedBy ?? "not recorded"}</dd>
          {chain && chain.runs.length > 1 && (
            <>
              <dt className="text-ink-muted">Chain</dt>
              <dd className="font-mono text-xs" data-testid="run-chain">
                {chain.runs.map((step) => step.stage ?? step.run_id).join(" → ")}
              </dd>
            </>
          )}
        </dl>
        <div>
          <p className="font-medium">Tools this agent may call</p>
          {tools.length === 0 ? (
            <p className="text-ink-muted">None: its version names no organisation connector the policy opens.</p>
          ) : (
            <>
              <ul className="mt-1 space-y-1">
                {tools.map((tool) => (
                  <li key={tool.name} data-testid={`tool-connector-${tool.name}`}>
                    <Link href={orgConnectorHref(tool.name)}>{tool.name}</Link>{" "}
                    <span className="text-xs text-ink-muted">
                      {tool.type} · as <span className="text-ink">{toolAccount(tool)}</span>
                    </span>
                  </li>
                ))}
              </ul>
              <p className="mt-1 text-xs text-ink-muted">
                The broker calls them with each connector&apos;s credential; the agent&apos;s pod never holds it.
              </p>
            </>
          )}
        </div>
      </div>
    </Card>
  );
}
