// SPDX-License-Identifier: Apache-2.0
"use client";

/**
 * The agent builder (S32-01, plan item 0): every field of an agent's version is editable here —
 * instructions, model, backend, limits, budget, skills, MCP servers and their tool patterns, knowledge
 * bases and memory scope — with, on the side, what each choice costs and allows. The definition stays data (`AgentSpec`); this is
 * a form on top of it, not a canvas.
 *
 * S39-05: it takes a full page. The basics (name, slug, description, instructions) are always open;
 * every other section folds and says its current value in one line, and opens on its own when it
 * holds something other than the default (`openByDefault`). The trial and what the draft costs sit
 * in a sticky panel on the right from 1280 px, under the form below.
 *
 * Loaded lazily (`next/dynamic`) by the pages that open it: it is the heaviest form of the console
 * and most visits never open it.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useId, useState, type ReactNode } from "react";
import { Button, Disclosure, ErrorNote, Input, Select, Textarea, tabClasses } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { scopeHint, scopeLabel } from "./registre";
import { EvalSuiteEditor, TrialBench } from "./trial";
import { usableModels } from "@/lib/usable-models";
import { usd } from "@/lib/format";
import type { Agent, AgentSpec, UsableModel, KnowledgeBase, OrgConnector, Skill } from "@/lib/types";

/** The model profiles a project maps to models (its settings, `models.yaml`). */
export const MODEL_PROFILES = ["standard", "strong", "cheap", "by_size"] as const;

/** Statuses of a skill that cannot be carried: archived, or not yet approved (S32-06). */
const SKILL_UNUSABLE = new Set(["archived", "pending_review", "rejected", "quarantined"]);

/** Whose memory a run reads and writes (S32-02); the API's default is the project's. */
export const MEMORY_SCOPES = {
  project: "The project's shared memory",
  agent: "Its own, within the project",
  none: "None",
} as const;

/** Who a knowledge base is served to: an agent reading it runs only in those projects. */
export function servedTo(base: KnowledgeBase): string {
  if (base.scope === "org") return "every project of the organisation";
  if (base.scope === "projects") return (base.projects ?? []).length ? `projects ${(base.projects ?? []).join(", ")}` : "no project yet";
  return (base.groups ?? []).length ? `the projects of ${(base.groups ?? []).join(", ")}` : "no group yet";
}

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
    budget: {
      ...spec.budget,
      run_usd: spec.budget?.run_usd ?? null,
      daily_usd: spec.budget?.daily_usd ?? null,
      monthly_usd: spec.budget?.monthly_usd ?? null,
    },
    skills: spec.skills ?? [],
    mcp_servers: (spec.mcp_servers ?? []).filter((s) => s.connector),
    knowledge: spec.knowledge ?? [],
    memory: spec.memory ?? "project",
    evals: (spec.evals ?? []).filter((c) => c.name.trim()),
  };
}

/** Slugs a page of the console already uses: `/agents/new` is a static route (S39-05). */
export const RESERVED_SLUGS = ["new"] as const;

/**
 * The slug an agent's name suggests: lowercase, accents stripped, anything else a dash, 2 to 63
 * characters, starting with a letter. A reserved slug, or a name that leaves nothing, gives "".
 */
export function slugFromName(name: string): string {
  let slug = name
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/['\u2019]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  if (/^[0-9]/.test(slug)) slug = `a-${slug}`;
  slug = slug.slice(0, 63).replace(/-+$/, "");
  if (slug.length < 2 || (RESERVED_SLUGS as readonly string[]).includes(slug)) return "";
  return slug;
}

/** Why a slug cannot be used, or null. */
export function slugProblem(slug: string): string | null {
  if (!slug) return null;
  if ((RESERVED_SLUGS as readonly string[]).includes(slug)) return `\`${slug}\` is reserved: it is a page of the console.`;
  if (!/^[a-z][a-z0-9-]{1,62}$/.test(slug)) return "Use lowercase letters, digits and dashes, starting with a letter (2 to 63 characters).";
  return null;
}

export type SectionId = "model" | "limits" | "skills" | "tools" | "knowledge" | "memory" | "evals";

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** One line per folded section: what it holds now, so a closed section still tells. */
export function sectionSummaries(spec: AgentSpec): Record<SectionId, string> {
  const limits = [
    spec.limits?.max_turns != null ? plural(spec.limits.max_turns, "turn", "turns") : null,
    spec.limits?.max_minutes != null ? `${spec.limits.max_minutes} min` : null,
    spec.budget?.run_usd != null ? `${usd(spec.budget.run_usd)} a run` : null,
    spec.budget?.daily_usd != null ? `${usd(spec.budget.daily_usd)} a day` : null,
    spec.budget?.monthly_usd != null ? `${usd(spec.budget.monthly_usd)} a month` : null,
  ].filter((part): part is string => part !== null);
  const skills = (spec.skills ?? []).length;
  const servers = (spec.mcp_servers ?? []).filter((s) => s.connector).length;
  const bases = (spec.knowledge ?? []).length;
  const cases = (spec.evals ?? []).length;
  return {
    model: `${spec.model || "the project's profile"} · ${spec.backend || "the project's backend"}`,
    limits: limits.length ? limits.join(" · ") : "the role's defaults",
    skills: skills ? `${skills} attached` : "none",
    tools: servers ? plural(servers, "MCP server", "MCP servers") : "none",
    knowledge: bases ? plural(bases, "knowledge base", "knowledge bases") : "none",
    memory: MEMORY_SCOPES[spec.memory ?? "project"].toLowerCase(),
    evals: plural(cases, "case", "cases"),
  };
}

/** A section starts open when it holds something other than the default. */
export function openByDefault(spec: AgentSpec): Record<SectionId, boolean> {
  const spent = [spec.budget?.run_usd, spec.budget?.daily_usd, spec.budget?.monthly_usd, spec.limits?.max_turns, spec.limits?.max_minutes];
  return {
    model: Boolean(spec.model || spec.backend),
    limits: spent.some((v) => v != null),
    skills: (spec.skills ?? []).length > 0,
    tools: (spec.mcp_servers ?? []).length > 0,
    knowledge: (spec.knowledge ?? []).length > 0,
    memory: Boolean(spec.memory && spec.memory !== "project"),
    evals: (spec.evals ?? []).length > 0,
  };
}

type Mode = { kind: "create" } | { kind: "version"; agent: Agent };

export function AgentBuilder({
  org,
  mode,
  start = {},
  initial,
  initialSample,
  scopes,
  onDone,
  onCreated,
  onBack,
}: {
  org: string;
  mode: Mode;
  /** The version one starts from: the latest, for a new version. */
  start?: AgentSpec;
  /** What the first step of the page collected (create mode). */
  /** The input "Try it" starts with, such as a drafted agent's sample (S39-05). */
  initialSample?: { title?: string; body?: string };
  initial?: { display_name?: string; slug?: string; description?: string; scope?: string; project?: string };
  /**
   * Create mode: who the agent may belong to, among what the person may create (ADR 0061) and the
   * projects to choose from. Absent, no scope is asked and the API's default (the organisation) applies.
   */
  scopes?: { options: Array<"org" | "project" | "personal">; projects: string[] };
  onDone?: (message: string) => void;
  /** Create mode: the agent that was registered, to go and read it. */
  onCreated?: (agent: Agent) => void;
  /** Create mode: the way back to the first step. */
  onBack?: () => void;
}) {
  const client = useQueryClient();
  const [name, setName] = useState(initial?.display_name ?? "");
  const [slug, setSlug] = useState(initial?.slug ?? slugFromName(initial?.display_name ?? ""));
  // The slug follows the name until a person edits it: it never changes once the agent exists.
  const [slugTouched, setSlugTouched] = useState(false);
  const [description, setDescription] = useState(initial?.description ?? "");
  const wanted = scopes?.options.find((s) => s === initial?.scope) ?? scopes?.options[0];
  const [scope, setScope] = useState<"org" | "project" | "personal" | undefined>(wanted);
  const [project, setProject] = useState(initial?.project ?? "");
  const projectChoice = project || scopes?.projects[0] || "";
  const [spec, setSpec] = useState<AgentSpec>(() => ({
    ...start,
    limits: { ...start.limits },
    budget: { ...start.budget },
    skills: [...(start.skills ?? [])],
    mcp_servers: (start.mcp_servers ?? []).map((s) => ({ ...s, tools: [...(s.tools ?? [])] })),
    knowledge: (start.knowledge ?? []).map((k) => ({ ...k })),
    evals: (start.evals ?? []).map((c) => ({ ...c })),
  }));
  const [error, setError] = useState<string | null>(null);
  const [failing, setFailing] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [panel, setPanel] = useState<"try" | "costs">("try");

  // Only the models the organisation lets an agent use (`/admin/models`, ADR 0060).
  const models = useQuery({ queryKey: ["usable-models", org], queryFn: () => usableModels(org, { usage: "agent" }), retry: false });
  const backends = useQuery({ queryKey: ["platform-backends"], queryFn: () => api.backends(), retry: false });
  const connectors = useQuery({ queryKey: ["org-connectors", org], queryFn: () => api.orgConnectors(org), retry: false });
  const library = useQuery({ queryKey: ["skills", org], queryFn: () => api.skills(org) });
  const bases = useQuery({ queryKey: ["knowledge", org], queryFn: () => api.knowledgeBases(org), retry: false });

  const mcpConnectors = (connectors.data ?? []).filter((c) => c.kind === "mcp");
  const set = (patch: Partial<AgentSpec>) => setSpec((s) => ({ ...s, ...patch }));
  const creating = mode.kind === "create";
  const problem = creating ? slugProblem(slug) : null;
  // What the form held when it opened decides which sections start open.
  const [opened] = useState(() => openByDefault(spec));
  const summaries = sectionSummaries(spec);

  async function submit(lifecycle: "active" | "draft" = "active") {
    setError(null);
    setFailing([]);
    setBusy(true);
    try {
      if (mode.kind === "create") {
        const agent = await api.createAgent(org, {
          slug,
          kind: "internal",
          display_name: name || slug,
          description: description.trim() || null,
          spec: cleanSpec(spec),
          lifecycle,
          ...(scope ? { scope, project: scope === "project" ? projectChoice : null } : {}),
        });
        await client.invalidateQueries({ queryKey: ["agents", org] });
        onDone?.(`${agent.display_name} registered as v${agent.latest_version}${lifecycle === "draft" ? ", as a draft" : ""}`);
        onCreated?.(agent);
      } else {
        const published = await api.publishAgentVersion(org, mode.agent.slug, cleanSpec(spec));
        onDone?.(`v${published.version} published: projects keep the version they pinned`);
        await client.invalidateQueries({ queryKey: ["agent", org, mode.agent.slug] });
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Refused");
      // A failing eval names its case (S32-03): the publication is refused, the case said.
      const errors = cause instanceof ApiError ? ((cause.problem.errors ?? []) as { loc?: unknown[]; msg?: string }[]) : [];
      setFailing(
        errors
          .filter((e) => Array.isArray(e.loc) && e.loc[0] === "evals")
          .map((e) => `${String(e.loc![1])}: ${e.msg ?? ""}`),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_26rem]" data-testid="agent-builder">
      <form
        className="min-w-0 space-y-4"
        aria-label={creating ? "New agent" : `New version of ${mode.agent.display_name}`}
        onSubmit={(event) => {
          event.preventDefault();
          if (!busy && !(creating && (!slug || problem))) void submit("active");
        }}
      >
        {creating && (
          <Section title="Basics">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Name">
                {(id) => (
                  <Input
                    id={id}
                    value={name}
                    onChange={(e) => {
                      setName(e.target.value);
                      if (!slugTouched) setSlug(slugFromName(e.target.value));
                    }}
                  />
                )}
              </Field>
              <Field label="Slug" hint={problem ?? "Lowercase letters, digits and dashes; it never changes once the agent exists."}>
                {(id) => (
                  <Input
                    id={id}
                    value={slug}
                    onChange={(e) => {
                      setSlugTouched(true);
                      setSlug(e.target.value);
                    }}
                    aria-invalid={problem ? true : undefined}
                    required
                  />
                )}
              </Field>
            </div>
            {scopes && scopes.options.length > 0 && scope && (
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Scope" hint={scopeHint(scope)}>
                  {(id) => (
                    <Select id={id} value={scope} onChange={(e) => setScope(e.target.value as "org" | "project" | "personal")}>
                      {scopes.options.map((s) => (
                        <option key={s} value={s}>
                          {scopeLabel(s)}
                        </option>
                      ))}
                    </Select>
                  )}
                </Field>
                {scope === "project" && (
                  <Field label="Project">
                    {(id) => (
                      <Select id={id} value={projectChoice} onChange={(e) => setProject(e.target.value)}>
                        {scopes.projects.map((p) => (
                          <option key={p} value={p}>
                            {p}
                          </option>
                        ))}
                      </Select>
                    )}
                  </Field>
                )}
              </div>
            )}
            <Field label="Description" hint="What the agent is for, in a sentence or two. People read it in the registry.">
              {(id) => (
                <Textarea id={id} rows={3} maxLength={4000} value={description} onChange={(e) => setDescription(e.target.value)} />
              )}
            </Field>
            <InstructionsField spec={spec} set={set} />
          </Section>
        )}
        {!creating && (
          <Section title="Instructions">
            <InstructionsField spec={spec} set={set} />
          </Section>
        )}

        <Disclosure title="Model and runtime" summary={summaries.model} defaultOpen={opened.model}>
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
                          {m.display_name || m.model_name}
                        </option>
                      ))}
                    </optgroup>
                  )}
                  {spec.model && !spec.model.startsWith("profile:") && !(models.data ?? []).some((m) => m.model_name === spec.model) && (
                    <option value={spec.model}>{spec.model} (disabled by the organisation)</option>
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
        </Disclosure>

        <SkillsSection library={library.data ?? []} spec={spec} set={set} summary={summaries.skills} defaultOpen={opened.skills} />

        <McpSection connectors={mcpConnectors} spec={spec} set={set} summary={summaries.tools} defaultOpen={opened.tools} />

        <KnowledgeSection bases={bases.data ?? []} spec={spec} set={set} summary={summaries.knowledge} defaultOpen={opened.knowledge} />

        <Disclosure title="Memory" summary={summaries.memory} defaultOpen={opened.memory}>
          <Field
            label="Memory scope"
            hint="What a run remembers from earlier runs, and where it writes what it learns. Memory is data in the context, never instructions."
          >
            {(id) => (
              <Select
                id={id}
                value={spec.memory ?? "project"}
                onChange={(e) => set({ memory: e.target.value as keyof typeof MEMORY_SCOPES })}
              >
                {Object.entries(MEMORY_SCOPES).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        </Disclosure>

        <Disclosure title="Limits and budget" summary={summaries.limits} defaultOpen={opened.limits}>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
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
            <Field label="Budget per month (USD)">
              {(id) => (
                <Input
                  id={id}
                  type="number"
                  min={0}
                  step="1"
                  value={spec.budget?.monthly_usd ?? ""}
                  onChange={(e) => set({ budget: { ...spec.budget, monthly_usd: numberOrNull(e.target.value) } })}
                />
              )}
            </Field>
          </div>
        </Disclosure>

        <Disclosure title="Eval suite" summary={summaries.evals} defaultOpen={opened.evals || failing.length > 0}>
          <EvalSuiteEditor cases={spec.evals ?? []} onChange={(evals) => set({ evals })} />
        </Disclosure>

        {error && <ErrorNote>{error}</ErrorNote>}
        {failing.length > 0 && (
          <ul className="space-y-1 text-sm text-failed-ink" aria-label="Failing eval cases" data-testid="evals-en-echec">
            {failing.map((f) => (
              <li key={f}>{f}</li>
            ))}
          </ul>
        )}

        <div
          className="sticky bottom-0 z-10 flex flex-wrap items-center gap-3 border-t border-line bg-surface/95 py-3 backdrop-blur-sm"
          data-testid="action-bar"
        >
          {creating ? (
            <>
              <Button type="submit" tone="primary" disabled={busy || !slug || problem !== null}>
                Create and activate
              </Button>
              <Button type="button" disabled={busy || !slug || problem !== null} onClick={() => void submit("draft")}>
                Save as draft
              </Button>
              {onBack && (
                <Button type="button" onClick={onBack}>
                  ← Back to the description
                </Button>
              )}
            </>
          ) : (
            <Button type="submit" tone="primary" disabled={busy}>
              {`Publish v${mode.agent.latest_version + 1}`}
            </Button>
          )}
        </div>
      </form>

      <aside
        className="min-w-0 space-y-3 xl:sticky xl:top-16 xl:max-h-[calc(100dvh-5rem)] xl:overflow-y-auto"
        aria-label="Try it and what it costs"
        data-testid="panneau-lateral"
      >
        <h2 className="sr-only">Trial and costs</h2>
        <div role="tablist" aria-label="Side panel" className="flex gap-6 border-b border-line">
          {(
            [
              ["try", "Try it"],
              ["costs", "What it costs and allows"],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              role="tab"
              id={`builder-tab-${id}`}
              aria-selected={panel === id}
              aria-controls={`builder-panel-${id}`}
              className={tabClasses(panel === id)}
              onClick={() => setPanel(id)}
            >
              {label}
            </button>
          ))}
        </div>
        {/* Both panels stay mounted: a trial under way is not lost when one looks at the costs. */}
        <div role="tabpanel" id="builder-panel-try" aria-labelledby="builder-tab-try" hidden={panel !== "try"}>
          <TrialBench
            spec={cleanSpec(spec)}
            agent={mode.kind === "version" ? mode.agent.slug : undefined}
            initialSample={initialSample}
          />
        </div>
        <div role="tabpanel" id="builder-panel-costs" aria-labelledby="builder-tab-costs" hidden={panel !== "costs"}>
          <BuilderSummary
            spec={spec}
            models={models.data ?? []}
            connectors={mcpConnectors}
            library={library.data ?? []}
            bases={bases.data ?? []}
          />
        </div>
      </aside>
    </div>
  );
}

function InstructionsField({ spec, set }: { spec: AgentSpec; set: (patch: Partial<AgentSpec>) => void }) {
  return (
    <div className="space-y-2">
      <Field label="Instructions" hint="Empty: the playbook of the role speaks.">
        {(id) => (
          <Textarea
            id={id}
            className="font-mono text-xs"
            rows={14}
            value={spec.instructions ?? ""}
            onChange={(e) => set({ instructions: e.target.value })}
          />
        )}
      </Field>
      <Disclosure title="Which variables can I use?" className="border-0" id="template-variables">
        <p className="text-xs text-ink-muted">
          The instructions are a template rendered in a sandbox: it reads <code>ticket</code>, <code>spec</code>,{" "}
          <code>plan_markdown</code>, <code>inputs</code>, <code>allowed_paths</code>, <code>context</code> and{" "}
          <code>project</code>, and nothing else. The platform appends its frame after it — the output contract and the
          invariants — which the instructions cannot change.
        </p>
      </Disclosure>
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
  summary,
  defaultOpen,
}: {
  library: Skill[];
  spec: AgentSpec;
  set: (patch: Partial<AgentSpec>) => void;
  summary: string;
  defaultOpen: boolean;
}) {
  const [adding, setAdding] = useState("");
  const [version, setVersion] = useState("");
  const carried = spec.skills ?? [];
  const chosen = library.find((s) => s.slug === adding);
  return (
    <Disclosure title="Skills" summary={summary} defaultOpen={defaultOpen}>
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
        <p className="text-xs text-ink-muted">
          Not in the library yet?{" "}
          <Link href="/skills/catalogue" className="underline">
            Browse the skills catalogue
          </Link>
          .
        </p>
      </div>
    </Disclosure>
  );
}

function McpSection({
  connectors,
  spec,
  set,
  summary,
  defaultOpen,
}: {
  connectors: OrgConnector[];
  spec: AgentSpec;
  set: (patch: Partial<AgentSpec>) => void;
  summary: string;
  defaultOpen: boolean;
}) {
  const servers = spec.mcp_servers ?? [];
  const [adding, setAdding] = useState("");
  const update = (index: number, tools: string[]) =>
    set({ mcp_servers: servers.map((s, i) => (i === index ? { ...s, tools } : s)) });
  return (
    <Disclosure title="Tools (MCP servers)" summary={summary} defaultOpen={defaultOpen}>
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
        <p className="text-xs text-ink-muted">
          No MCP connector in the organisation yet: an administrator declares one, or installs one from{" "}
          <Link href="/admin/connectors/catalogue" className="underline">
            the MCP catalogue
          </Link>
          .
        </p>
      )}
    </Disclosure>
  );
}

function KnowledgeSection({
  bases,
  spec,
  set,
  summary,
  defaultOpen,
}: {
  bases: KnowledgeBase[];
  spec: AgentSpec;
  set: (patch: Partial<AgentSpec>) => void;
  summary: string;
  defaultOpen: boolean;
}) {
  const [adding, setAdding] = useState("");
  const read = spec.knowledge ?? [];
  return (
    <Disclosure title="Knowledge" summary={summary} defaultOpen={defaultOpen}>
      <div data-testid="bases-de-la-version" className="space-y-2">
        {read.length === 0 ? (
          <p className="text-xs text-ink-muted">None — it reads no knowledge base.</p>
        ) : (
          <ul className="flex flex-wrap gap-2">
            {read.map((k) => (
              <li key={k.slug} className="inline-flex items-center gap-1 rounded border border-line px-2 py-0.5 text-xs">
                <span className="font-mono">{k.slug}</span>
                <button
                  type="button"
                  className="ml-1 text-ink-muted hover:text-ink"
                  onClick={() => set({ knowledge: read.filter((c) => c.slug !== k.slug) })}
                >
                  Remove<span className="sr-only"> {k.slug}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="flex flex-wrap items-end gap-2">
          <label className="flex min-w-0 flex-col gap-1">
            <span className="text-xs text-ink-muted">Add a knowledge base</span>
            <Select value={adding} onChange={(e) => setAdding(e.target.value)}>
              <option value="">—</option>
              {bases
                .filter((b) => !read.some((k) => k.slug === b.slug))
                .map((b) => (
                  <option key={b.slug} value={b.slug}>
                    {b.name} ({b.serving_mode})
                  </option>
                ))}
            </Select>
          </label>
          <Button
            size="sm"
            disabled={!adding}
            onClick={() => {
              set({ knowledge: [...read, { slug: adding }] });
              setAdding("");
            }}
          >
            Read it
          </Button>
        </div>
        {bases.length === 0 && <p className="text-xs text-ink-muted">No knowledge base in the organisation yet.</p>}
        <p className="text-xs text-ink-muted">
          Not there?{" "}
          <a href="/knowledge" target="_blank" rel="noreferrer" className="underline">
            Create a knowledge base
          </a>{" "}
          (opens in a new tab), then come back.
        </p>
      </div>
    </Disclosure>
  );
}

/** What the draft costs and allows, said next to the form (plan item 0). */
export function BuilderSummary({
  spec,
  models,
  connectors,
  library,
  bases = [],
}: {
  spec: AgentSpec;
  models: UsableModel[];
  connectors: OrgConnector[];
  library: Skill[];
  bases?: KnowledgeBase[];
}) {
  const model = models.find((m) => m.model_name === spec.model);
  const servers = spec.mcp_servers ?? [];
  return (
    <section className="raised h-fit space-y-4 rounded-md border border-line p-4 text-sm" aria-label="What this version costs and allows" data-testid="ce-que-ca-permet">
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
            {model.output_cost_per_1k != null ? `${usd(model.output_cost_per_1k)} out` : "?"} per 1,000 tokens
            {model.priced_by === "override" ? " (the organisation's price)." : "."}
          </p>
        ) : (
          <p>
            <span className="font-mono">{spec.model}</span>: disabled by the organisation, or not a model of the gateway — a run
            would fail to resolve it, and a new version cannot name it.
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
          {spec.budget?.monthly_usd != null
            ? ` Alerts at 50, 80 and 100 % of ${usd(spec.budget.monthly_usd)} a month; suspended at 100 %.`
            : ""}
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
      <div className="space-y-1" data-testid="connaissances-resolues">
        <h4 className="text-xs font-medium text-ink-muted">Knowledge</h4>
        {(spec.knowledge ?? []).length === 0 ? (
          <p>None.</p>
        ) : (
          <ul className="text-xs">
            {(spec.knowledge ?? []).map((k) => {
              const base = bases.find((b) => b.slug === k.slug);
              return (
                <li key={k.slug}>
                  <span className="font-mono">{k.slug}</span> —{" "}
                  {!base
                    ? "not a base of the organisation: a run would stop."
                    : `${base.serving_mode === "index" ? "searched with knowledge_search" : "laid read-only in the workspace"}; served to ${servedTo(base)}.`}
                </li>
              );
            })}
          </ul>
        )}
        {(spec.knowledge ?? []).length > 0 && (
          <p className="text-xs text-ink-muted">A run in a project a base is not served to stops before it starts.</p>
        )}
      </div>
      <div className="space-y-1">
        <h4 className="text-xs font-medium text-ink-muted">Memory</h4>
        <p data-testid="memoire-resolue">
          {spec.memory === "none"
            ? "No memory: a run neither reads nor writes what earlier runs learnt."
            : spec.memory === "agent"
              ? "Its own memory within each project: what it learns stays with it."
              : "The project's shared memory: it reads and adds to what every agent of the project learnt."}
        </p>
      </div>
    </section>
  );
}
