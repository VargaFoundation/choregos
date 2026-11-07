// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { settingsHref } from "@/components/project-settings/groups";
import { Alert, Badge, Card, Code, ReadState } from "@/components/ui";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";

/** `max_concurrent_calls` when the policy does not say (policy.schema.json). */
const DEFAULT_CONCURRENT_CALLS = 4;

/** How the project's card names the organisation's switch (S57-05b). */
const ORG_CHOICE_LABELS = { enabled: "enabled", disabled: "disabled", default: "the deployment's default" } as const;

/**
 * Whether this project's agents may call their tools from a script (ADR 0122 §6, S57-10): the
 * organisation's switch (S57-05b), the policy's `tools` section and the organisation's ceiling,
 * read-only. The policy is edited as YAML in the guardrails; a form here would be a second editor of the
 * same document. The organisation's switch is its administrator's, in Administration.
 */
export function ToolScriptsCard({ slug }: { slug: string }) {
  const { org, orgs } = useSession();
  const orgChoice = (orgs ?? []).find((o) => o.slug === org)?.tool_scripts ?? null;
  const orgDisabled = orgChoice === "disabled";
  const policy = useQuery({ queryKey: ["policy", slug], queryFn: () => api.policy(slug) });
  const ceiling = useQuery({ queryKey: ["policy-ceiling", org], queryFn: () => api.policyCeiling(org), retry: false });
  const tools = policy.data?.json?.tools;
  // Absent: the policy says nothing, and an agent whose catalogue entry offers them by default gets them (S57-15).
  const said = tools?.scripts ?? null;
  const scripts = said ?? "off";
  const concurrent = tools?.max_concurrent_calls;
  const roles = Object.entries(tools?.per_role ?? {});
  const forbidden = ceiling.data?.ceiling?.tools?.scripts === "off";
  const clamps = (policy.data?.clamps ?? []).filter((clamp) => clamp.startsWith("tools."));
  return (
    <Card title="Tool scripts">
      {!policy.data ? (
        <ReadState lecture={policy} quoi="the policy" />
      ) : (
        <div className="space-y-3 text-sm" data-testid="tool-scripts">
          <p className="text-ink-muted">
            With <Code>offer</Code>, an agent may also call its run&apos;s tools from a script it writes, through the runner&apos;s
            tool bridge, and read only what the script prints. Each call is governed as the model&apos;s own: the same allowlist,
            cap, approvals and ledger.
          </p>
          {orgDisabled && (
            <Alert tone="warn" title="The organisation has tool scripts disabled">
              No run of this project is offered them, whatever this policy says, until an organisation administrator switches
              them on.
            </Alert>
          )}
          {forbidden && (
            <Alert tone="warn" title="The organisation forbids tool scripts">
              No run of this project is offered them, whatever this policy says: a policy that offers them is refused when it
              is saved.
            </Alert>
          )}
          <dl className="grid grid-cols-[minmax(0,12rem)_minmax(0,1fr)] gap-x-4 gap-y-2">
            <dt className="text-ink-muted">Scripts</dt>
            <dd data-testid="tool-scripts-value">
              <Badge tone={scripts === "offer" && !forbidden && !orgDisabled ? "info" : "neutral"}>{scripts}</Badge>
              {(forbidden || orgDisabled) && scripts === "offer" && <span className="ml-2 text-xs text-ink-muted">runs get off</span>}
              {said === null && !forbidden && !orgDisabled && (
                <span className="block text-xs text-ink-muted" data-testid="tool-scripts-agent-default">
                  not set: an agent whose catalogue entry offers them by default still gets them
                </span>
              )}
            </dd>
            <dt className="text-ink-muted">Organisation</dt>
            <dd data-testid="tool-scripts-org">
              {ORG_CHOICE_LABELS[orgChoice ?? "default"]}
              {orgChoice === null && <span className="block text-xs text-ink-muted">its operator&apos;s choice, which this page does not see</span>}
            </dd>
            <dt className="text-ink-muted">Calls at once</dt>
            <dd className="tabular-nums" data-testid="tool-scripts-concurrency">
              {concurrent ?? DEFAULT_CONCURRENT_CALLS}
              {concurrent === undefined && <span className="text-xs text-ink-muted"> (the default)</span>}
              <span className="block text-xs text-ink-muted">calls of one run the bridge relays together; the next one waits</span>
            </dd>
            {roles.length > 0 && (
              <>
                <dt className="text-ink-muted">Per role</dt>
                <dd>
                  <ul className="space-y-0.5 font-mono text-xs" data-testid="tool-scripts-roles">
                    {roles.map(([role, override]) => (
                      <li key={role}>
                        {role}: {[override.scripts, override.max_concurrent_calls && `${override.max_concurrent_calls} at once`].filter(Boolean).join(", ") || "as the project"}
                      </li>
                    ))}
                  </ul>
                </dd>
              </>
            )}
          </dl>
          {clamps.length > 0 && (
            <ul className="list-disc pl-5 text-xs" data-testid="tool-scripts-clamps">
              {clamps.map((clamp) => (
                <li key={clamp}>{clamp}</li>
              ))}
            </ul>
          )}
          {ceiling.error ? (
            <p className="text-xs text-ink-muted">The organisation&apos;s ceiling could not be read: whether it forbids tool scripts is not known here.</p>
          ) : null}
          <p className="text-xs text-ink-muted" data-testid="tool-scripts-edit">
            {forbidden ? (
              <>Only an organisation administrator can lift the ban, in the organisation&apos;s policy ceiling.</>
            ) : orgDisabled ? (
              <>Only an organisation administrator can switch them on, in Administration (Tool scripts).</>
            ) : (
              <>
                Set in the project&apos;s policy, <Code>tools.scripts</Code>, <Code>tools.max_concurrent_calls</Code> and{" "}
                <Code>tools.per_role</Code>:{" "}
                <Link href={settingsHref(slug, "policy")} className="underline">
                  edit the policy
                </Link>
                .
              </>
            )}{" "}
            The deployment&apos;s operator can also refuse them to every organisation; this page does not see that brake.
          </p>
        </div>
      )}
    </Card>
  );
}
