// SPDX-License-Identifier: Apache-2.0
"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";
import { Alert, Badge, Button, Card, Disclosure, Field, Select, buttonClasses } from "@/components/ui";
import { normalizeSpec } from "@/lib/agent-diff";
import { canEdit, currentVersion, previousVersion, sortedVersions, versionOf } from "@/lib/agent-versions";
import { relative, usd } from "@/lib/format";
import type { Agent, AgentSpec, AgentVersion } from "@/lib/types";
import { AgentDetailsCard } from "./details";
import { RestoreDialog } from "./history";
import { MEMORY_SCOPES, sectionSummaries } from "./spec-summary";

/**
 * One version of an agent, read-only and grouped (S40-12, ADR 0076). People read an agent far more often
 * than they edit it, so the Definition tab opens on this and editing is a deliberate mode. Every field
 * of the spec is shown, in a group that says its value in one line even when it is folded; a field this
 * console does not know is shown as JSON rather than dropped.
 */

/** Lines of instructions shown before "Show all". */
export const INSTRUCTION_LINES = 12;

const KNOWN_FIELDS = new Set(["instructions", "model", "backend", "limits", "budget", "skills", "mcp_servers", "knowledge", "memory", "evals"]);

const here = (slug: string, query: string) => `/agents/${slug}?${query}`;

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-xs text-ink-muted">{label}</dt>
      <dd className="text-sm">{children}</dd>
    </>
  );
}

function InstructionsText({ text }: { text: string }) {
  const [all, setAll] = useState(false);
  if (!text) return <p className="text-xs text-ink-muted">No instructions: the playbook of its role speaks.</p>;
  const lines = text.split("\n");
  const long = lines.length > INSTRUCTION_LINES;
  return (
    <div className="space-y-2">
      <pre aria-label="Instructions" className="whitespace-pre-wrap break-words rounded bg-surface-muted p-3 text-xs">
        {long && !all ? lines.slice(0, INSTRUCTION_LINES).join("\n") : text}
      </pre>
      {long && (
        <button type="button" className="text-xs underline" aria-expanded={all} onClick={() => setAll(!all)}>
          {all ? "Show fewer lines" : `Show all ${lines.length} lines`}
        </button>
      )}
    </div>
  );
}

/** Which revisions projects pin, and the bar's other facts. */
function pins(agent: Agent): Array<{ project: string; version: number }> {
  return sortedVersions(agent).flatMap((v) => (v.pinned_by ?? []).map((project) => ({ project, version: v.version })));
}

function VersionBar({ agent, shown }: { agent: Agent; shown: AgentVersion }) {
  const router = useRouter();
  const current = currentVersion(agent);
  const may = canEdit(agent);
  const before = previousVersion(agent, shown.version);
  const pinned = pins(agent);
  const suffix = shown.version === current ? "" : `&version=${shown.version}`;
  return (
    <Card data-testid="version-bar">
      <div className="space-y-3">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span className="font-mono text-lg font-semibold">v{shown.version}</span>
          {shown.version === current ? (
            <Badge tone="ok">Current</Badge>
          ) : (
            <Badge tone="neutral">Older</Badge>
          )}
          {shown.restored_from != null && <Badge tone="accent">{`Restored from v${shown.restored_from}`}</Badge>}
          <span className="text-xs text-ink-muted">
            {shown.created_by ? `published by ${shown.created_by}` : "published"}
            {shown.created_at ? ` ${relative(shown.created_at)}` : ""}
          </span>
        </div>
        <p className="text-sm" data-testid="version-message">
          {shown.message ? `“${shown.message}”` : <span className="text-ink-muted">No message (published before revisions had one).</span>}
        </p>
        {shown.version === current && (
          <p className="text-xs text-ink-muted">Current: what a run loads unless a project pins another revision.</p>
        )}
        {pinned.length > 0 && (
          <p className="text-xs text-ink-muted" data-testid="pinned">
            Pinned by {pinned.map((p) => `${p.project} (v${p.version})`).join(", ")}
          </p>
        )}
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Version">
            {(props) => (
              <Select
                {...props}
                value={shown.version}
                onChange={(e) => {
                  const n = Number(e.target.value);
                  router.push(n === current ? here(agent.slug, "tab=definition") : here(agent.slug, `tab=definition&version=${n}`));
                }}
              >
                {sortedVersions(agent).map((v) => (
                  <option key={v.version} value={v.version}>
                    {`v${v.version}${v.version === current ? " — Current" : ""}${v.message ? ` — “${v.message.length > 40 ? `${v.message.slice(0, 39)}…` : v.message}”` : ""}`}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          {may ? (
            <Link href={here(agent.slug, `tab=definition&edit=1${suffix}`)} className={buttonClasses("primary", "md")}>
              Edit
            </Link>
          ) : (
            <Button disabled aria-describedby="why-readonly">
              Edit
            </Button>
          )}
          {before && (
            <Link href={here(agent.slug, `tab=history&compare=${before.version}..${shown.version}`)} className={buttonClasses("secondary", "md")}>
              {`Compare with v${before.version}`}
            </Link>
          )}
        </div>
        {!may && (
          <p id="why-readonly" className="text-sm text-ink-muted" data-testid="why-readonly">
            {agent.cannot_reason ?? "You may not change this agent."}
          </p>
        )}
      </div>
    </Card>
  );
}

export function DefinitionView({ org, agent, version }: { org: string; agent: Agent; version: number | null }) {
  const current = currentVersion(agent);
  const shown = (version != null ? versionOf(agent, version) : undefined) ?? versionOf(agent, current) ?? sortedVersions(agent)[0];
  const [restoring, setRestoring] = useState(false);
  if (!shown) return <p className="text-sm text-ink-muted">No version</p>;
  const spec: AgentSpec = shown.spec;
  const normal = normalizeSpec(spec);
  const summaries = sectionSummaries(spec);
  const unknown = Object.entries(normal).filter(([key]) => !KNOWN_FIELDS.has(key));
  const lines = (spec.instructions ?? "") === "" ? 0 : (spec.instructions ?? "").split("\n").length;
  const older = shown.version !== current;
  const may = canEdit(agent);
  return (
    <div className="space-y-4">
      <VersionBar agent={agent} shown={shown} />
      {older && (
        <Alert tone="warn" title={`You are reading v${shown.version}`}>
          <p className="text-sm">
            v{current} is current.{" "}
            <Link href={here(agent.slug, `tab=history&compare=${shown.version}..${current}`)} className="underline">
              {`Compare with v${current}`}
            </Link>
          </p>
          <div className="mt-2">
            <Button size="sm" disabled={!may} onClick={() => setRestoring(true)}>
              {`Restore as v${agent.latest_version + 1}`}
            </Button>
          </div>
        </Alert>
      )}
      {restoring && <RestoreDialog org={org} agent={agent} version={shown.version} open onClose={() => setRestoring(false)} />}

      <div className="space-y-3" data-testid="definition-groups">
        <Disclosure title="About" summary={`${agent.display_name}${(agent.tags ?? []).length ? ` · ${(agent.tags ?? []).join(", ")}` : ""}`}>
          <AgentDetailsCard bare org={org} agent={agent} />
        </Disclosure>

        <Disclosure title="Instructions" summary={lines ? `${lines} line${lines === 1 ? "" : "s"}` : "none"} defaultOpen>
          <InstructionsText text={spec.instructions ?? ""} />
        </Disclosure>

        <Disclosure title="Model and runtime" summary={summaries.model}>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5">
            <Row label="Model">{spec.model ?? "The project's profile"}</Row>
            <Row label="Backend">{spec.backend ?? "The project's"}</Row>
          </dl>
        </Disclosure>

        <Disclosure
          title="Capabilities"
          summary={`skills ${summaries.skills} · tools ${summaries.tools} · knowledge ${summaries.knowledge} · memory ${summaries.memory}`}
        >
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5">
            <Row label="Skills">
              {(spec.skills ?? []).length === 0
                ? "None"
                : (spec.skills ?? []).map((s) => (
                    <Link key={s.slug} href={`/skills/${s.slug}`} className="mr-2 underline">
                      {s.slug}
                      {s.version ? `@${s.version}` : ""}
                    </Link>
                  ))}
            </Row>
            <Row label="Tools">
              {(spec.mcp_servers ?? []).length === 0 ? (
                "Those of the platform (or of its human)"
              ) : (
                <ul>
                  {(spec.mcp_servers ?? []).map((s) => (
                    <li key={s.connector}>
                      <span className="font-mono">{s.connector}</span>
                      <span className="text-ink-muted"> {"—"} {(s.tools ?? []).length ? (s.tools ?? []).join(", ") : "every tool"}</span>
                    </li>
                  ))}
                </ul>
              )}
            </Row>
            <Row label="Knowledge">
              {(spec.knowledge ?? []).length === 0 ? "None" : (spec.knowledge ?? []).map((k) => k.slug).join(", ")}
            </Row>
            <Row label="Memory">{MEMORY_SCOPES[spec.memory ?? "project"]}</Row>
          </dl>
        </Disclosure>

        <Disclosure title="Limits and budget" summary={summaries.limits}>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5">
            <Row label="Max turns">{spec.limits?.max_turns ?? "The role's default"}</Row>
            <Row label="Max minutes">{spec.limits?.max_minutes ?? "The role's default"}</Row>
            <Row label="Per run">{spec.budget?.run_usd != null ? usd(spec.budget.run_usd) : "No cap"}</Row>
            <Row label="Per day">{spec.budget?.daily_usd != null ? usd(spec.budget.daily_usd) : "No cap"}</Row>
            <Row label="Per month">{spec.budget?.monthly_usd != null ? usd(spec.budget.monthly_usd) : "No cap"}</Row>
          </dl>
        </Disclosure>

        <Disclosure title="Eval suite" summary={summaries.evals}>
          {(spec.evals ?? []).length === 0 ? (
            <p className="text-sm text-ink-muted">No case: nothing gates the next revision.</p>
          ) : (
            <ul className="space-y-1 text-sm">
              {(spec.evals ?? []).map((c) => (
                <li key={c.name}>
                  <span className="font-medium">{c.name}</span>
                  <span className="text-xs text-ink-muted">
                    {" "}
                    {"—"} must contain {(c.must_contain ?? []).length}, must not contain {(c.must_not_contain ?? []).length}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Disclosure>

        {unknown.length > 0 && (
          <Disclosure title="Other fields" summary={unknown.map(([key]) => key).join(", ")}>
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5">
              {unknown.map(([key, value]) => (
                <Row key={key} label={key}>
                  <code className="break-all text-xs">{JSON.stringify(value)}</code>
                </Row>
              ))}
            </dl>
          </Disclosure>
        )}
      </div>
    </div>
  );
}
