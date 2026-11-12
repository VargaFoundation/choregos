// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { BookOpen, Brain, Cpu, Gauge, Library, Wrench, type LucideIcon } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";
import { useDocsUrl } from "@/components/docs-link";
import { ProviderLogo, providerMark } from "@/components/provider-logo";
import { Badge, Card, IconTile } from "@/components/ui";
import { currentVersion, versionOf } from "@/lib/agent-versions";
import { api } from "@/lib/api";
import { usd } from "@/lib/format";
import { harnessOf } from "@/lib/harness";
import { describe, effectiveBindings, IMPLICIT } from "@/lib/memories";
import { groupBySize, resolveModel, type ResolvedModel } from "@/lib/model-resolution";
import { skillHref } from "@/lib/skill-slug";
import type { Agent, AgentSpec, KnowledgeBase, MemoryStore, ModelCatalogue, Skill } from "@/lib/types";
import { GROUP_HUES } from "@/components/shell/navigation";
import { INHERITED, inheritsMemory, missingComponents, toolSources } from "./spec-summary";

/**
 * The components of the current version in one card on the Overview tab (S60-07, ADR 0127; redone after the
 * product owner's review of 2026-11-12): an agent is read first here, so a flat list of slugs and a
 * "none (closed)" that looked like "uses nothing" made a working agent look empty.
 *
 * It reads the version's spec, and four lists the console already caches (model catalogue, knowledge bases,
 * skills, memories) to say what each reference IS: the model a request resolves to for this organisation,
 * a knowledge base's size, a skill's description. A list that fails to load leaves the bare reference, a
 * fact the catalogue does not hold is left out, never guessed.
 */

const LABEL: Record<string, string> = {
  model: "model",
  "tools.catalogue": "catalogue tools",
  "tools.extensions": "extensions",
  "tools.web": "web tools",
  memories: "memory",
};

/** The sidebar's icons (`shell/navigation.tsx`): knowledge Library, skills BookOpen, memories Brain. */
const HUE = GROUP_HUES.define;

function Section({ icon, title, testid, children, aside }: { icon: LucideIcon; title: string; testid: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className="space-y-2" aria-labelledby={`${testid}-title`} data-testid={testid}>
      <div className="flex items-center gap-2">
        <IconTile icon={icon} hue={HUE} />
        <h3 id={`${testid}-title`} className="text-sm font-semibold">
          {title}
        </h3>
        {aside}
      </div>
      <div className="space-y-1.5 pl-8 text-sm">{children}</div>
    </section>
  );
}

const Muted = ({ children, testid }: { children: ReactNode; testid?: string }) => (
  <p className="text-ink-muted" data-testid={testid}>
    {children}
  </p>
);

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

function ModelRow({ sizes, scaled, row }: { sizes: string[]; scaled: Array<{ size: string; factor: number }>; row: ResolvedModel }) {
  const mark = providerMark(row.provider);
  const shown = row.upstream && row.upstream !== row.displayName ? row.upstream : null;
  return (
    <li className="flex flex-wrap items-center gap-x-2 gap-y-1" data-testid={`composition-model-${row.modelName}`}>
      {sizes.length > 0 && <span className="w-14 shrink-0 text-xs text-ink-muted">{`Size ${sizes.join(", ")}`}</span>}
      <ProviderLogo provider={row.provider} />
      <span className="font-medium">{row.displayName}</span>
      {row.profile && <span className="font-mono text-xs text-ink-muted">{`${row.profile} · ${row.modelName}`}</span>}
      {shown && <span className="font-mono text-xs text-ink-muted">{shown}</span>}
      {mark && <span className="text-xs text-ink-muted">{mark.label}</span>}
      {row.reasoning === true && <Badge tone="info">reasoning model</Badge>}
      {row.reasoning === false && <Badge tone="neutral">no reasoning</Badge>}
      {scaled.map((x) => (
        <span key={x.size} className="text-xs text-ink-muted">{`${x.size}: turn limit × ${x.factor}`}</span>
      ))}
    </li>
  );
}

function ModelSection({ spec, catalogue }: { spec: AgentSpec; catalogue: ModelCatalogue | undefined }) {
  const requested = spec.model?.trim();
  const groups = groupBySize(resolveModel(requested, catalogue));
  const harness = harnessOf(spec);
  return (
    <Section icon={Cpu} title="Model" testid="composition-model">
      {requested ? (
        <>
          <p>
            <span className="text-xs text-ink-muted">Requested </span>
            <span className="font-mono">{requested}</span>
            {requested === "profile:by_size" && <span className="text-xs text-ink-muted">{" — the model follows the work item's size"}</span>}
          </p>
          {catalogue ? (
            <ul className="space-y-1.5" data-testid="composition-model-resolved">
              {groups.map(({ row, sizes, scaled }) => (
                <ModelRow key={row.modelName} row={row} sizes={sizes} scaled={scaled} />
              ))}
            </ul>
          ) : (
            <Muted>{"Which model it resolves to in this organisation is not loaded."}</Muted>
          )}
        </>
      ) : (
        <p>{INHERITED}</p>
      )}
      {harness.kind === "coding" && (
        <p className="text-xs text-ink-muted" data-testid="composition-harness">
          {`Carried by ${harness.backend ? `the ${harness.backend} coding agent` : "the project's coding agent (claude-code unless the project sets another)"}.`}
        </p>
      )}
    </Section>
  );
}

function ToolLink({ href, children, external, testid }: { href: string; children: ReactNode; external?: boolean; testid: string }) {
  return external ? (
    <a href={href} target="_blank" rel="noreferrer" className="font-mono underline" data-testid={testid}>
      {children}
    </a>
  ) : (
    <Link href={href} className="font-mono underline" data-testid={testid}>
      {children}
    </Link>
  );
}

function Origin({ title, testid, children }: { title: string; testid: string; children: ReactNode }) {
  return (
    <div data-testid={testid}>
      <p className="text-xs font-medium text-ink-muted">{title}</p>
      <div className="mt-0.5">{children}</div>
    </div>
  );
}

function ToolsSection({ spec }: { spec: AgentSpec }) {
  const docsTools = useDocsUrl("build/tools");
  const harness = harnessOf(spec);
  const sources = toolSources(spec);
  const servers = (spec.mcp_servers ?? []).filter((s) => s.connector);
  const none = <span className="text-ink-muted">none declared</span>;
  const named = (id: "catalogue" | "extensions" | "web") => sources.find((s) => s.id === id)!.named;
  const list = (id: "catalogue" | "extensions" | "web", item: (name: string) => ReactNode) => {
    const names = named(id);
    if (names === null) return <span className="text-ink-muted">{INHERITED}</span>;
    if (names.length === 0) return none;
    return (
      <span className="flex flex-wrap gap-x-3 gap-y-0.5">
        {names.map((name) => (
          <span key={name}>{item(name)}</span>
        ))}
      </span>
    );
  };
  return (
    <Section icon={Wrench} title="Tools" testid="composition-tools">
      <p className="text-xs text-ink-muted" data-testid="composition-tools-legend">
        {"“none declared” means the version names no tool from that source: the run is offered none of it, whatever its project allows. “Inherited” leaves the source to the project."}
      </p>
      <Origin title="Built in to the runtime" testid="composition-tools-builtin">
        {harness.kind === "none" ? (
          <Muted testid="composition-builtin-none">{`None. ${harness.reason}`}</Muted>
        ) : (
          <>
            <ul className="space-y-0.5" data-testid="composition-builtin-tools">
              {harness.tools.map((tool) => (
                <li key={tool.id} data-testid={`composition-builtin-${tool.id}`}>
                  <span className="font-medium">{tool.label}</span> <span className="text-ink-muted">{tool.detail}</span>
                </li>
              ))}
            </ul>
          </>
        )}
      </Origin>
      <Origin title="Platform tools" testid="composition-tools-platform">
        <ul className="space-y-0.5">
          <li data-testid="composition-tools-catalogue">
            <span className="text-ink-muted">Catalogue </span>
            {list("catalogue", (name) => (
              <ToolLink href={docsTools} external testid={`composition-tool-catalogue-${name}`}>
                {name}
              </ToolLink>
            ))}
          </li>
          <li data-testid="composition-tools-extensions">
            <span className="text-ink-muted">Extensions </span>
            {list("extensions", (name) => (
              <ToolLink href="/library/plugins" testid={`composition-tool-extension-${name}`}>
                {name}
              </ToolLink>
            ))}
          </li>
          <li data-testid="composition-tools-web">
            <span className="text-ink-muted">Web </span>
            {list("web", (name) => (
              <span className="font-mono">{name === "fetch" ? "web fetch" : "web search"}</span>
            ))}
          </li>
        </ul>
      </Origin>
      <Origin title="MCP servers" testid="composition-tools-mcp">
        {servers.length === 0 ? (
          none
        ) : (
          <ul className="space-y-0.5">
            {servers.map((server) => (
              <li key={server.connector}>
                <ToolLink href={`/admin/connectors?connector=${encodeURIComponent(server.connector)}`} testid={`composition-mcp-${server.connector}`}>
                  {server.connector}
                </ToolLink>
                {(server.tools ?? []).length > 0 && <span className="text-xs text-ink-muted">{` · ${plural(server.tools!.length, "tool", "tools")} granted`}</span>}
              </li>
            ))}
          </ul>
        )}
      </Origin>
    </Section>
  );
}

function KnowledgeSection({ spec, bases }: { spec: AgentSpec; bases: KnowledgeBase[] | undefined }) {
  const used = spec.knowledge ?? [];
  return (
    <Section icon={Library} title="Knowledge" testid="composition-knowledge">
      {used.length === 0 ? (
        <Muted>No knowledge base attached.</Muted>
      ) : (
        <ul className="space-y-1">
          {used.map((k) => {
            const base = bases?.find((b) => b.slug === k.slug);
            return (
              <li key={k.slug} data-testid={`composition-kb-${k.slug}`}>
                <Link href={`/knowledge/${k.slug}`} className="font-medium underline">
                  {base?.name ?? k.slug}
                </Link>
                {base && (
                  <span className="text-xs text-ink-muted">{` · ${plural(base.document_count ?? 0, "document", "documents")} · ${base.serving_mode === "index" ? "searched by index" : "files in the workspace"}`}</span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </Section>
  );
}

function SkillsSection({ spec, skills }: { spec: AgentSpec; skills: Skill[] | undefined }) {
  const used = spec.skills ?? [];
  return (
    <Section icon={BookOpen} title="Skills" testid="composition-skills">
      {used.length === 0 ? (
        <Muted>No skill attached.</Muted>
      ) : (
        <ul className="space-y-1">
          {used.map((s) => {
            const skill = skills?.find((x) => x.slug === s.slug);
            return (
              <li key={s.slug} data-testid={`composition-skill-${s.slug}`}>
                <Link href={skillHref(s.slug)} className="font-medium underline">
                  {s.slug}
                </Link>
                <span className="text-xs text-ink-muted">{s.version ? ` @${s.version}` : ""}</span>
                {skill?.description && <span className="block text-xs text-ink-muted">{skill.description}</span>}
              </li>
            );
          })}
        </ul>
      )}
    </Section>
  );
}

function MemorySection({ spec, stores }: { spec: AgentSpec; stores: MemoryStore[] | undefined }) {
  const bindings = effectiveBindings(spec);
  return (
    <Section icon={Brain} title="Memory" testid="composition-memory">
      {inheritsMemory(spec) ? (
        <p>{INHERITED}</p>
      ) : bindings.length === 0 ? (
        <Muted>None: it reads and writes nothing.</Muted>
      ) : (
        <ul className="space-y-1">
          {bindings.map((b) => {
            const name = describe(b, stores);
            const implicit = b.scope === "project" && b.slug in IMPLICIT;
            const detail = `${b.scope === "global" ? "organisation" : "project"} · ${b.mode === "write" ? "reads and writes" : "reads"}`;
            return (
              <li key={`${b.scope}:${b.slug}`} data-testid={`composition-memory-${b.scope}-${b.slug}`}>
                {b.scope === "global" ? (
                  <Link href={`/memories/${b.slug}`} className="font-medium underline">
                    {name}
                  </Link>
                ) : (
                  <span className="font-medium">{name}</span>
                )}
                <span className="text-xs text-ink-muted">{` · ${detail}${implicit ? " (every project has it)" : ""}`}</span>
              </li>
            );
          })}
        </ul>
      )}
    </Section>
  );
}

function LimitsSection({ spec }: { spec: AgentSpec }) {
  const parts: Array<[string, string]> = [];
  if (spec.limits?.max_turns != null) parts.push(["Turns", `${spec.limits.max_turns} a run`]);
  if (spec.limits?.max_minutes != null) parts.push(["Time", `${spec.limits.max_minutes} min a run`]);
  if (spec.budget?.run_usd != null) parts.push(["Budget", `${usd(spec.budget.run_usd)} a run`]);
  if (spec.budget?.daily_usd != null) parts.push(["", `${usd(spec.budget.daily_usd)} a day`]);
  if (spec.budget?.monthly_usd != null) parts.push(["", `${usd(spec.budget.monthly_usd)} a month`]);
  return (
    <Section icon={Gauge} title="Limits and budget" testid="composition-limits">
      {parts.length === 0 ? (
        <Muted>{"None set by the version: the role's defaults apply."}</Muted>
      ) : (
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5">
          {parts.map(([label, value]) => (
            <div key={value} className="contents">
              <dt className="text-xs text-ink-muted">{label}</dt>
              <dd className="font-mono">{value}</dd>
            </div>
          ))}
        </dl>
      )}
    </Section>
  );
}

export function AgentComposition({ org, agent }: { org: string; agent: Agent }) {
  const spec = versionOf(agent, currentVersion(agent))?.spec;
  // The lists below only sharpen the answer: the spec alone already says everything it declares.
  const catalogue = useQuery({ queryKey: ["model-catalogue", org], queryFn: () => api.modelCatalogue(org), retry: false, enabled: Boolean(spec?.model) });
  const bases = useQuery({ queryKey: ["knowledge", org], queryFn: () => api.knowledgeBases(org), retry: false, enabled: (spec?.knowledge ?? []).length > 0 });
  const skills = useQuery({ queryKey: ["skills", org], queryFn: () => api.skills(org), retry: false, enabled: (spec?.skills ?? []).length > 0 });
  const stores = useQuery({
    queryKey: ["memories", org, "global"],
    queryFn: () => api.memories(org, { scope: "global" }),
    retry: false,
    enabled: (spec?.memories ?? []).some((b) => b.scope === "global"),
  });
  if (!spec) return null;
  const missing = missingComponents(spec);
  const base = `/agents/${agent.slug}`;
  return (
    <Card title="Composition" data-testid="agent-composition">
      <div className="space-y-5">
        <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
          <span>
            Version <span className="font-mono">v{currentVersion(agent)}</span>
          </span>
          {missing.length === 0 ? (
            <Badge tone="ok" data-testid="composition-complete">
              complete
            </Badge>
          ) : (
            <Badge tone="warn" data-testid="composition-inherits">
              {`inherits: ${missing.map((m) => LABEL[m] ?? m).join(", ")}`}
            </Badge>
          )}
        </p>
        <div className="grid gap-x-8 gap-y-5 lg:grid-cols-2">
          <div className="space-y-5">
            <ModelSection spec={spec} catalogue={catalogue.data} />
            <ToolsSection spec={spec} />
          </div>
          <div className="space-y-5">
            <KnowledgeSection spec={spec} bases={bases.data} />
            <SkillsSection spec={spec} skills={skills.data} />
            <MemorySection spec={spec} stores={stores.data} />
            <LimitsSection spec={spec} />
          </div>
        </div>
        <p className="flex flex-wrap gap-x-4 text-sm">
          <Link href={`${base}?tab=definition`} className="underline" data-testid="composition-definition-link">
            Every field of this version
          </Link>
          <Link href={`${base}?tab=access`} className="underline" data-testid="composition-access-link">
            What it reaches in a project
          </Link>
        </p>
      </div>
    </Card>
  );
}
