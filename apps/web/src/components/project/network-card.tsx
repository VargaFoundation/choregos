// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useSectionWrites } from "@/components/project-settings/section";
import { Badge, Button, Card, ErrorNote, EtatDeLecture, Field, Input, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { flaggedByCeiling, parseHosts, type CeilingNetwork } from "@/lib/network";
import { withSandboxNetwork, type SandboxNetwork } from "@/lib/policy-network";

/**
 * The network a project's runs really get (ADR 0070): its own `sandbox.network` under the
 * organisation's ceiling, plus the host of its repository, which a run needs to work at all. The
 * project's side is edited here (S41-22: the card used to be read-only and sent the reader to "the
 * policy below", which sat above it); the organisation's is on the Administration page.
 */
export function ProjectNetworkCard({ slug }: { slug: string }) {
  const network = useQuery({ queryKey: ["project-network", slug], queryFn: () => api.projectNetwork(slug), retry: false });
  const effective = network.data?.effective;
  return (
    <Card title="Network access" className="lg:col-span-2">
      <div className="space-y-3 text-sm" data-testid="project-network">
        <EtatDeLecture lecture={network} quoi="the network access" />
        {effective && (
          <>
            <div>
              <h3 className="text-xs font-medium text-ink-muted">Allowed hosts</h3>
              {effective.allow.length === 0 && effective.implied.length === 0 ? (
                <p>No internet: a run reaches no host outside the platform.</p>
              ) : (
                <ul className="mt-1 flex flex-wrap gap-1.5">
                  {effective.allow.map((host) => (
                    <li key={host}>
                      <Badge tone="neutral">
                        <span className="font-mono text-xs">{host}</span>
                      </Badge>
                    </li>
                  ))}
                  {effective.implied.map((host) => (
                    <li key={`implied-${host}`}>
                      <Badge tone="accent">
                        <span className="font-mono text-xs">{host}</span> · repository
                      </Badge>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div>
              <h3 className="text-xs font-medium text-ink-muted">Denied hosts</h3>
              {effective.deny.length === 0 ? (
                <p className="text-ink-muted">None.</p>
              ) : (
                <ul className="mt-1 flex flex-wrap gap-1.5">
                  {effective.deny.map((host) => (
                    <li key={host}>
                      <Badge tone="danger">
                        <span className="font-mono text-xs">{host}</span>
                      </Badge>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
              <dt className="text-ink-muted">Requests per minute, per host</dt>
              <dd>{effective.per_host_per_minute ?? "no cap"}</dd>
              <dt className="text-ink-muted">Requests per minute, per run</dt>
              <dd>{effective.per_run_per_minute ?? "no cap"}</dd>
              <dt className="text-ink-muted">Built-in web tools</dt>
              <dd>{effective.builtin_web_tools ? "Allowed" : "Off"}</dd>
            </dl>
            {(network.data?.clamps ?? []).length > 0 && (
              <div data-testid="network-clamps">
                <p>The organisation&apos;s network access brings this project down:</p>
                <ul className="list-disc pl-5 text-xs">
                  {(network.data?.clamps ?? []).map((clamp) => (
                    <li key={clamp}>{clamp}</li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
        {network.data && (
          <ProjectNetworkEditor
            slug={slug}
            stored={network.data.policy as SandboxNetwork}
            ceiling={(network.data.ceiling ?? null) as CeilingNetwork | null}
          />
        )}
      </div>
    </Card>
  );
}

/** A cap typed in a field: empty is no cap, anything but a whole number from 1 is refused. */
function cap(text: string, label: string): { value: number | null; problem: string | null } {
  if (!text.trim()) return { value: null, problem: null };
  const value = Number(text);
  if (Number.isInteger(value) && value >= 1) return { value, problem: null };
  return { value: null, problem: `${label}: a whole number from 1, or empty for no cap` };
}

/** The project's own `sandbox.network`, written into its policy: the server validates it and clamps it under the ceiling. */
function ProjectNetworkEditor({ slug, stored, ceiling }: { slug: string; stored: SandboxNetwork; ceiling: CeilingNetwork | null }) {
  const policy = useQuery({ queryKey: ["policy", slug], queryFn: () => api.policy(slug) });
  const { faire, notice } = useSectionWrites(slug);
  const [allowText, setAllowText] = useState(() => (stored.allow_domains ?? []).join("\n"));
  const [denyText, setDenyText] = useState(() => (stored.deny_domains ?? []).join("\n"));
  const [perHost, setPerHost] = useState(() => String(stored.per_host_per_minute ?? ""));
  const [perRun, setPerRun] = useState(() => String(stored.per_run_per_minute ?? ""));
  const [webTools, setWebTools] = useState(() => stored.builtin_web_tools !== "deny");
  const [refused, setRefused] = useState<string | null>(null);

  const allow = parseHosts(allowText);
  const deny = parseHosts(denyText);
  const host = cap(perHost, "Per-host cap (requests per minute)");
  const run = cap(perRun, "Per-run cap (requests per minute)");
  const problems = [...allow.problems, ...deny.problems, host.problem, run.problem].filter((p): p is string => p !== null);
  // What the organisation's ceiling refuses: the server refuses the whole policy for it (422), this only says so first.
  const flagged = flaggedByCeiling(allow.hosts, ceiling).map((f) => ({ key: f.host, what: <span className="font-mono">{f.host}</span>, reason: f.reason }));
  for (const [name, label, typed] of [
    ["per_host_per_minute", "Per-host cap (requests per minute)", host.value],
    ["per_run_per_minute", "Per-run cap (requests per minute)", run.value],
  ] as const) {
    const limit = ceiling?.[name];
    if (limit != null && (typed === null || typed > limit)) flagged.push({ key: name, what: <>{label}</>, reason: `the organisation caps it at ${limit}` });
  }
  if (ceiling?.builtin_web_tools === "deny" && webTools) {
    flagged.push({ key: "builtin_web_tools", what: <>Built-in web tools</>, reason: "the organisation switches them off" });
  }

  function save() {
    setRefused(null);
    const next: SandboxNetwork = {
      ...stored,
      allow_domains: allow.hosts,
      deny_domains: deny.hosts,
      per_host_per_minute: host.value,
      per_run_per_minute: run.value,
      builtin_web_tools: webTools ? "allow" : "deny",
    };
    const out = withSandboxNetwork(policy.data?.yaml ?? "", next);
    if (!out.ok) {
      setRefused(out.reason);
      return;
    }
    void faire(() => api.putPolicy(slug, out.yaml), "Network access saved", ["policy", "project-network"]);
  }

  return (
    <div className="space-y-3 border-t border-line pt-3" data-testid="project-network-editor">
      <h3 className="text-xs font-medium text-ink-muted">This project&apos;s network access</h3>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field
          label="Allowed hosts"
          hint="One per line. A host covers its subdomains, *.example.com only the subdomains, * any public host. None: no internet."
        >
          {(p) => (
            <Textarea {...p} rows={4} value={allowText} onChange={(e) => setAllowText(e.target.value)} placeholder={"pypi.org\napi.github.com"} />
          )}
        </Field>
        <Field label="Denied hosts" hint="One per line. A denied host wins over an allowed one.">
          {(p) => <Textarea {...p} rows={4} value={denyText} onChange={(e) => setDenyText(e.target.value)} />}
        </Field>
        <Field label="Per-host cap (requests per minute)" hint="Empty: the project sets no cap of its own.">
          {(p) => <Input {...p} type="number" min={1} value={perHost} onChange={(e) => setPerHost(e.target.value)} />}
        </Field>
        <Field label="Per-run cap (requests per minute)" hint="Empty: the project sets no cap of its own.">
          {(p) => <Input {...p} type="number" min={1} value={perRun} onChange={(e) => setPerRun(e.target.value)} />}
        </Field>
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={webTools} onChange={(e) => setWebTools(e.target.checked)} />
        Agents may use their CLI&apos;s built-in web tools (WebFetch, WebSearch)
      </label>
      {problems.length > 0 && (
        <ul className="list-disc pl-5 text-xs text-danger" data-testid="project-network-problems">
          {problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      )}
      {flagged.length > 0 && (
        <ul className="list-disc pl-5 text-xs text-failed-ink" data-testid="project-network-flagged">
          {flagged.map((f) => (
            <li key={f.key}>
              {f.what}: {f.reason}. Saving is refused until it is within.
            </li>
          ))}
        </ul>
      )}
      {refused && <ErrorNote>{refused}</ErrorNote>}
      {notice}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-ink-muted">
          Saved into the project&apos;s policy, as <code>sandbox.network</code>. The organisation&apos;s part is on the
          Administration page.
        </p>
        <Button tone="primary" onClick={save} disabled={problems.length > 0 || !policy.data}>
          Save the network access
        </Button>
      </div>
    </div>
  );
}
