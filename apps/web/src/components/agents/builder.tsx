// SPDX-License-Identifier: Apache-2.0
"use client";

/**
 * The agent builder (S32-01, plan item 0): every field of an agent's version is editable here —
 * instructions, model, backend, limits, budget, skills, MCP servers and their tool patterns — with,
 * on the side, what each choice costs and allows. The definition stays data (`AgentSpec`); this is
 * a form on top of it, not a canvas.
 *
 * Loaded lazily (`next/dynamic`) by the pages that open it: it is the heaviest form of the console
 * and most visits never open it.
 *
 * Follow-up (not on this base): knowledge bases (`AgentSpec.knowledge`, #418) and the memory scope
 * (S32-02) get their sections once those fields exist in the generated contract types.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type ReactNode } from "react";
import { Button, ErrorNote, Input, Select, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { usd } from "@/lib/format";
import type { Agent, AgentSpec, GatewayModel, OrgConnector, Skill } from "@/lib/types";

/** The model profiles a project maps to models (its settings, `models.yaml`). */
export const MODEL_PROFILES = ["standard", "strong", "cheap", "by_size"] as const;

/** Statuses of a skill that cannot be carried: archived, or not yet approved (S32-06). */
const SKILL_UNUSABLE = new Set(["archived", "pending_review", "rejected", "quarantined"]);

export function skillUsable(skill: Skill): boolean {
  return !SKILL_UNUSABLE.has(skill.status);
}

/** A tool pattern — a name, or a `prefix_*` — matches an operation's name. */
export function toolMatches(pattern: string, name: string): boolean {
  const p = pattern.trim();
  if (!p) return false;
  return p.endsWith("*") ? name.startsWith(p.slice(0, -1)) : name === p;
}

/** The operations of a connector an MCP server entry lets the agent call. */
export function reachedOperations(connector: OrgConnector | undefined, tools: string[]) {
  return (connector?.operations ?? []).filter((op) => tools.some((t) => toolMatches(t, op.name)));
}

function parseTools(text: string): string[] {
  return text
    .split(/[\s,]+/)
    .map((t) => t.trim())
    .filter(Boolean);
}

function numberOrNull(text: string): number | null {
  if (text.trim() === "") return null;
  const n = Number(text);
  return Number.isFinite(n) ? n : null;
}

/** The spec as the API takes it: empty fields left out, so the project's defaults apply. */
export function cleanSpec(spec: AgentSpec): AgentSpec {
  return {
    ...spec,
    instructions: spec.instructions ?? "",
    model: spec.model || null,
    backend: spec.backend || null,
    limits: { max_turns: spec.limits?.max_turns ?? null, max_minutes: spec.limits?.max_minutes ?? null },
    budget: { ...spec.budget, run_usd: spec.budget?.run_usd ?? null, daily_usd: spec.budget?.daily_usd ?? null },
    skills: spec.skills ?? [],
    mcp_servers: (spec.mcp_servers ?? []).filter((s) => s.connector),
  };
}

type Mode = { kind: "create" } | { kind: "version"; agent: Agent };

export function AgentBuilder({
  org,
  mode,
  start = {},
  onDone,
  extra,
}: {
  org: string;
  mode: Mode;
  /** The version one starts from: the latest, for a new version. */
  start?: AgentSpec;
  onDone: (message: string) => void;
  /** Rendered under the form, with the draft spec: the "try it" bench (S32-03). */
  extra?: (spec: AgentSpec) => ReactNode;
}) {
  const client = useQueryClient();
  const [slug, setSlug] = useState("");
  const [name, setName] = useState("");
  const [spec, setSpec] = useState<AgentSpec>(() => ({
    ...start,
    limits: { ...start.limits },
    budget: { ...start.budget },
    skills: [...(start.skills ?? [])],
    mcp_servers: (start.mcp_servers ?? []).map((s) => ({ ...s, tools: [...(s.tools ?? [])] })),
  }));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const models = useQuery({ queryKey: ["platform-models"], queryFn: () => api.platformModels(), retry: false });
  const backends = useQuery({ queryKey: ["platform-backends"], queryFn: () => api.backends(), retry: false });
  const connectors = useQuery({ queryKey: ["org-connectors", org], queryFn: () => api.orgConnectors(org), retry: false });
  const library = useQuery({ queryKey: ["skills", org], queryFn: () => api.skills(org) });

  const mcpConnectors = (connectors.data ?? []).filter((c) => c.kind === "mcp");
  const set = (patch: Partial<AgentSpec>) => setSpec((s) => ({ ...s, ...patch }));
  const creating = mode.kind === "create";

  async function submit() {
    setError(null);
    setBusy(true);
    try {
      if (mode.kind === "create") {
        const agent = await api.createAgent(org, {
          slug,
          kind: "internal",
          display_name: name || slug,
          spec: cleanSpec(spec),
        });
        onDone(`${agent.display_name} registered as v${agent.latest_version}`);
        await client.invalidateQueries({ queryKey: ["agents", org] });
      } else {
        const published = await api.publishAgentVersion(org, mode.agent.slug, cleanSpec(spec));
        onDone(`v${published.version} published: projects keep the version they pinned`);
        await client.invalidateQueries({ queryKey: ["agent", org, mode.agent.slug] });
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Refused");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_18rem]" data-testid="agent-builder">
      <form
        className="space-y-5"
        aria-label={creating ? "New agent" : `New version of ${mode.agent.display_name}`}
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        {creating && (
          <Section title="Identity">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Slug" hint="Lowercase letters, digits and dashes; it never changes.">
                {(id) => (
                  <Input id={id} value={slug} onChange={(e) => setSlug(e.target.value)} pattern="[a-z][a-z0-9-]{1,62}" required />
                )}
              </Field>
              <Field label="Name">{(id) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} />}</Field>
            </div>
          </Section>
        )}

        <Section title="Instructions">
          <Field
            label="Instructions"
            hint={
              <>
                A template rendered in a sandbox: it reads <code>ticket</code>, <code>spec</code>,{" "}
                <code>plan_markdown</code>, <code>inputs</code>, <code>allowed_paths</code>, <code>context</code> and{" "}
                <code>project</code>, and nothing else. The platform appends its frame after it — the output contract and
                the invariants — which the instructions cannot change. Empty: the playbook of the role speaks.
              </>
            }
          >
            {(id) => (
              <Textarea
                id={id}
                className="font-mono text-xs"
                rows={8}
                value={spec.instructions ?? ""}
                onChange={(e) => set({ instructions: e.target.value })}
              />
            )}
          </Field>
        </Section>

        <Section title="Model and backend">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Model" hint="A profile follows the project's mapping; a model is fixed.">
              {(id) => (
                <Select id={id} value={spec.model ?? ""} onChange={(e) => set({ model: e.target.value || null })}>
                  <option value="">The project&apos;s profile</option>
                  <optgroup label="Profiles">
                    {MODEL_PROFILES.map((p) => (
                      <option key={p} value={`profile:${p}`}>
                        profile:{p}
                      </option>
                    ))}
                  </optgroup>
                  {(models.data ?? []).length > 0 && (
                    <optgroup label="Models">
                      {(models.data ?? []).map((m) => (
                        <option key={m.model_name} value={m.model_name}>
                          {m.model_name}
                        </option>
                      ))}
                    </optgroup>
                  )}
                  {spec.model && !spec.model.startsWith("profile:") && !(models.data ?? []).some((m) => m.model_name === spec.model) && (
                    <option value={spec.model}>{spec.model}</option>
                  )}
                </Select>
              )}
            </Field>
            <Field label="Backend" hint="The agent runtime; empty: the project's default.">
              {(id) =>
                (backends.data ?? []).length > 0 ? (
                  <Select id={id} value={spec.backend ?? ""} onChange={(e) => set({ backend: e.target.value || null })}>
                    <option value="">The project&apos;s default</option>
                    {(backends.data ?? []).map((b) => (
                      <option key={b.name} value={b.name} disabled={!b.enabled}>
                        {b.name}
                        {b.enabled ? "" : " (disabled)"}
                      </option>
                    ))}
                  </Select>
                ) : (
                  <Input id={id} value={spec.backend ?? ""} onChange={(e) => set({ backend: e.target.value || null })} placeholder="claude-code" />
                )
              }
            </Field>
          </div>
        </Section>

        <Section title="Limits and budget">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Field label="Max turns">
              {(id) => (
                <Input
                  id={id}
                  type="number"
                  min={1}
                  max={1000}
                  value={spec.limits?.max_turns ?? ""}
                  onChange={(e) => set({ limits: { ...spec.limits, max_turns: numberOrNull(e.target.value) } })}
                />
              )}
            </Field>
            <Field label="Max minutes">
              {(id) => (
                <Input
                  id={id}
                  type="number"
                  min={1}
                  max={480}
                  value={spec.limits?.max_minutes ?? ""}
                  onChange={(e) => set({ limits: { ...spec.limits, max_minutes: numberOrNull(e.target.value) } })}
                />
              )}
            </Field>
            <Field label="Budget per run (USD)">
              {(id) => (
                <Input
                  id={id}
                  type="number"
                  min={0}
                  step="0.1"
                  value={spec.budget?.run_usd ?? ""}
                  onChange={(e) => set({ budget: { ...spec.budget, run_usd: numberOrNull(e.target.value) } })}
                />
              )}
            </Field>
            <Field label="Budget per day (USD)">
              {(id) => (
                <Input
                  id={id}
                  type="number"
                  min={0}
                  step="0.5"
                  value={spec.budget?.daily_usd ?? ""}
                  onChange={(e) => set({ budget: { ...spec.budget, daily_usd: numberOrNull(e.target.value) } })}
                />
              )}
            </Field>
          </div>
        </Section>

        <SkillsSection library={library.data ?? []} spec={spec} set={set} />

        <McpSection connectors={mcpConnectors} spec={spec} set={set} />

        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" tone="primary" disabled={busy || (creating && !slug)}>
            {creating ? "Create the agent" : `Publish v${mode.agent.latest_version + 1}`}
          </Button>
        </div>
        {error && <ErrorNote>{error}</ErrorNote>}
        {extra?.(cleanSpec(spec))}
      </form>
      <BuilderSummary spec={spec} models={models.data ?? []} connectors={mcpConnectors} library={library.data ?? []} />
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <fieldset className="space-y-3 rounded-md border border-line p-4">
      <legend className="px-1 text-sm font-semibold text-ink">{title}</legend>
      {children}
    </fieldset>
  );
}

function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: (id: string) => ReactNode }) {
  const id = useId();
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-xs text-ink-muted">
        {label}
      </label>
      {children(id)}
      {hint && <p className="text-xs text-ink-muted">{hint}</p>}
    </div>
  );
}

function SkillsSection({
  library,
  spec,
  set,
}: {
  library: Skill[];
  spec: AgentSpec;
  set: (patch: Partial<AgentSpec>) => void;
}) {
  const [adding, setAdding] = useState("");
  const [version, setVersion] = useState("");
  const carried = spec.skills ?? [];
  const chosen = library.find((s) => s.slug === adding);
  return (
    <Section title="Skills">
      <div data-testid="skills-de-la-version" className="space-y-2">
        {carried.length === 0 ? (
          <p className="text-xs text-ink-muted">None — the playbook of its role is all it reads.</p>
        ) : (
          <ul className="flex flex-wrap gap-2">
            {carried.map((s) => (
              <li key={s.slug} className="inline-flex items-center gap-1 rounded border border-line px-2 py-0.5 text-xs">
                <span className="font-mono">
                  {s.slug}
                  {s.version ? `@${s.version}` : " (latest)"}
                </span>
                <button
                  type="button"
                  className="ml-1 text-ink-muted hover:text-ink"
                  onClick={() => set({ skills: carried.filter((c) => c.slug !== s.slug) })}
                >
                  Remove<span className="sr-only"> {s.slug}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="flex flex-wrap items-end gap-2">
          <label className="flex min-w-0 flex-col gap-1">
            <span className="text-xs text-ink-muted">Add a skill</span>
            <Select
              value={adding}
              onChange={(e) => {
                setAdding(e.target.value);
                setVersion("");
              }}
            >
              <option value="">—</option>
              {library
                .filter((s) => !carried.some((c) => c.slug === s.slug))
                .map((s) => (
                  <option key={s.slug} value={s.slug} disabled={!skillUsable(s)}>
                    {s.slug}
                    {skillUsable(s) ? "" : ` (${s.status.replace(/_/g, " ")})`}
                  </option>
                ))}
            </Select>
          </label>
          {chosen && (
            <label className="flex min-w-0 flex-col gap-1">
              <span className="text-xs text-ink-muted">Version</span>
              <Select value={version} onChange={(e) => setVersion(e.target.value)}>
                <option value="">Latest, read at each run</option>
                {Array.from({ length: chosen.latest_version }, (_, i) => chosen.latest_version - i).map((v) => (
                  <option key={v} value={v}>
                    v{v}, pinned
                  </option>
                ))}
              </Select>
            </label>
          )}
          <Button
            size="sm"
            disabled={!chosen || !skillUsable(chosen)}
            onClick={() => {
              if (!chosen) return;
              set({ skills: [...carried, version ? { slug: chosen.slug, version: Number(version) } : { slug: chosen.slug }] });
              setAdding("");
              setVersion("");
            }}
          >
            Attach
          </Button>
        </div>
      </div>
    </Section>
  );
}

function McpSection({
  connectors,
  spec,
  set,
}: {
  connectors: OrgConnector[];
  spec: AgentSpec;
  set: (patch: Partial<AgentSpec>) => void;
}) {
  const servers = spec.mcp_servers ?? [];
  const [adding, setAdding] = useState("");
  const update = (index: number, tools: string[]) =>
    set({ mcp_servers: servers.map((s, i) => (i === index ? { ...s, tools } : s)) });
  return (
    <Section title="Tools (MCP servers)">
      <p className="text-xs text-ink-muted">
        The organisation&apos;s MCP connectors, and the tools of each the agent may call: names, or patterns such as{" "}
        <code>search_*</code>. None: the tools the platform gives a run of its project.
      </p>
      <ul className="space-y-3" data-testid="serveurs-mcp">
        {servers.map((server, index) => (
          <li key={server.connector} className="space-y-1 rounded border border-line p-3">
            <div className="flex items-center justify-between gap-2">
              <span className="font-mono text-sm">{server.connector}</span>
              <Button size="sm" onClick={() => set({ mcp_servers: servers.filter((_, i) => i !== index) })}>
                Remove<span className="sr-only"> {server.connector}</span>
              </Button>
            </div>
            <label className="block space-y-1">
              <span className="text-xs text-ink-muted">Tool patterns of {server.connector}</span>
              <Input
                defaultValue={(server.tools ?? []).join(", ")}
                onChange={(e) => update(index, parseTools(e.target.value))}
                placeholder="track_*, search_orders"
                className="font-mono"
              />
            </label>
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex min-w-0 flex-col gap-1">
          <span className="text-xs text-ink-muted">Add a server</span>
          <Select value={adding} onChange={(e) => setAdding(e.target.value)}>
            <option value="">—</option>
            {connectors
              .filter((c) => !servers.some((s) => s.connector === c.name))
              .map((c) => (
                <option key={c.name} value={c.name}>
                  {c.name}
                </option>
              ))}
          </Select>
        </label>
        <Button
          size="sm"
          disabled={!adding}
          onClick={() => {
            set({ mcp_servers: [...servers, { connector: adding, tools: [] }] });
            setAdding("");
          }}
        >
          Add
        </Button>
      </div>
      {connectors.length === 0 && (
        <p className="text-xs text-ink-muted">No MCP connector in the organisation yet: an administrator declares one.</p>
      )}
    </Section>
  );
}

/** What the draft costs and allows, said next to the form (plan item 0). */
export function BuilderSummary({
  spec,
  models,
  connectors,
  library,
}: {
  spec: AgentSpec;
  models: GatewayModel[];
  connectors: OrgConnector[];
  library: Skill[];
}) {
  const model = models.find((m) => m.model_name === spec.model);
  const servers = spec.mcp_servers ?? [];
  return (
    <aside className="raised h-fit space-y-4 rounded-md border border-line p-4 text-sm lg:sticky lg:top-16" aria-label="What this version costs and allows" data-testid="ce-que-ca-permet">
      <h3 className="text-sm font-semibold">What it costs and allows</h3>
      <div className="space-y-1">
        <h4 className="text-xs font-medium text-ink-muted">Model</h4>
        {!spec.model ? (
          <p>The project&apos;s profile for its role and size decides.</p>
        ) : spec.model.startsWith("profile:") ? (
          <p>
            <span className="font-mono">{spec.model}</span>: each project maps it to a model in its settings.
          </p>
        ) : model ? (
          <p>
            <span className="font-mono">{model.model_name}</span>:{" "}
            {model.input_cost_per_1k != null ? `${usd(model.input_cost_per_1k)} in` : "?"} ·{" "}
            {model.output_cost_per_1k != null ? `${usd(model.output_cost_per_1k)} out` : "?"} per 1,000 tokens.
          </p>
        ) : (
          <p>
            <span className="font-mono">{spec.model}</span>: not a model of the gateway — a run would fail to resolve it.
          </p>
        )}
      </div>
      <div className="space-y-1">
        <h4 className="text-xs font-medium text-ink-muted">Spending</h4>
        <p>
          {spec.budget?.run_usd != null ? `A run stops at ${usd(spec.budget.run_usd)}` : "A run stops at the project's budget for its role"}
          {spec.budget?.daily_usd != null
            ? `; no new run once ${usd(spec.budget.daily_usd)} is spent in a day.`
            : "; no daily cap of its own."}
        </p>
        <p>
          {spec.limits?.max_turns != null ? `${spec.limits.max_turns} turns` : "The role's turns"},{" "}
          {spec.limits?.max_minutes != null ? `${spec.limits.max_minutes} minutes` : "the role's minutes"} at most. A project
          may only tighten these.
        </p>
      </div>
      <div className="space-y-1">
        <h4 className="text-xs font-medium text-ink-muted">Skills</h4>
        <p>
          {(spec.skills ?? []).length === 0
            ? "None."
            : `${(spec.skills ?? []).length} skill(s), laid where its backend reads them and checked against their digest.`}
        </p>
        {(spec.skills ?? []).some((s) => {
          const found = library.find((l) => l.slug === s.slug);
          return found && !skillUsable(found);
        }) && <p className="text-failed-ink">A skill it carries is not usable: the run would stop.</p>}
      </div>
      <div className="space-y-1" data-testid="outils-resolus">
        <h4 className="text-xs font-medium text-ink-muted">Tools</h4>
        {servers.length === 0 ? (
          <p>No MCP server of its own: the tools the platform gives a run of its project.</p>
        ) : (
          servers.map((server) => {
            const connector = connectors.find((c) => c.name === server.connector);
            const reached = reachedOperations(connector, server.tools ?? []);
            return (
              <div key={server.connector}>
                <p className="font-mono text-xs">{server.connector}</p>
                {reached.length === 0 ? (
                  <p className="text-xs text-ink-muted">No operation matches its patterns.</p>
                ) : (
                  <ul className="text-xs">
                    {reached.map((op) => (
                      <li key={op.name}>
                        <span className="font-mono">{op.name}</span> —{" "}
                        {op.policy === "allowed" ? "called freely" : op.policy === "approval" ? "each call asks a person" : "refused"}
                        {op.price_usd ? `, ${usd(op.price_usd)} a call` : ""}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })
        )}
        <p className="text-xs text-ink-muted">
          At run time the broker intersects these with the project&apos;s grants (a project only tightens) and, for an
          external agent, with its human&apos;s rights.
        </p>
      </div>
    </aside>
  );
}
