// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import dynamic from "next/dynamic";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { use, useState } from "react";
import { LifecycleBadges, LifecyclePanel } from "@/components/agents/lifecycle";
import { estUnClientMcp, etatDuClient, resumeDeLaVersion } from "@/components/agents/registre";
import {
  Badge,
  Breadcrumbs,
  Button,
  Card,
  Empty,
  ErrorNote,
  EtatDeLecture,
  Heading,
  Select,
  StateBadge,
  Table,
  TBody,
  TD,
  TH,
  THead,
  TR,
  Tabs,
  tabClasses,
} from "@/components/ui";
import { api } from "@/lib/api";
import { percent, relative, shortDate, usd } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { Agent, AgentSpec } from "@/lib/types";

// The builder is loaded when the Definition tab is opened (S32-01).
const AgentBuilder = dynamic(() => import("@/components/agents/builder").then((m) => m.AgentBuilder), {
  loading: () => <p className="text-sm text-ink-muted">Loading the builder…</p>,
});

/** The tabs of an agent's page (S30-05, plan item 7): one subject each. */
const AGENT_TABS = [
  { id: "overview", label: "Overview" },
  { id: "definition", label: "Definition" },
  { id: "versions", label: "Versions" },
  { id: "runs", label: "Runs" },
  { id: "access", label: "Access" },
] as const;
type AgentTab = (typeof AGENT_TABS)[number]["id"];

/**
 * An agent of the registry, in tabs (S30-05): what it did and cost (overview), what it is (its current
 * version, and publishing the next), its versions, its runs, and what it may reach (access — and,
 * for an external agent, the clients that act as it). The tab is in the address: a link names it.
 */
export default function AgentPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const { org } = useSession();
  const pathname = usePathname();
  const search = useSearchParams();
  const demande = search.get("tab");
  const onglet: AgentTab = AGENT_TABS.some((o) => o.id === demande) ? (demande as AgentTab) : "overview";
  const agent = useQuery({ queryKey: ["agent", org, slug], queryFn: () => api.agent(org, slug) });
  if (agent.error) return <ErrorNote>{String(agent.error)}</ErrorNote>;
  if (!agent.data) return <Empty>Reading the agent…</Empty>;
  const a = agent.data;
  return (
    <div className="space-y-6">
      <Breadcrumbs steps={[{ href: "/agents", label: "Agents" }, { label: <span data-donnee>{a.display_name}</span> }]} />
      <div className="flex flex-wrap items-baseline gap-3">
        <Heading as="h1" size="xl" data-donnee>
          {a.display_name}
        </Heading>
        <span className="font-mono text-sm text-ink-muted">{a.slug}</span>
        <Badge tone={a.kind === "external" ? "accent" : "neutral"}>{a.kind === "external" ? "External" : "Internal"}</Badge>
        <Badge tone={a.status === "active" ? "ok" : "danger"}>{a.status}</Badge>
        <LifecycleBadges agent={a} />
        {a.owner && <span className="text-xs text-ink-muted">Owned by {a.owner}</span>}
        {a.expires_at && <span className="text-xs text-ink-muted">Expires {shortDate(a.expires_at)}</span>}
      </div>
      <Tabs aria-label={`Sections of ${a.display_name}`}>
        {AGENT_TABS.map((o) => (
          <Link
            key={o.id}
            href={o.id === "overview" ? pathname : `${pathname}?tab=${o.id}`}
            aria-current={o.id === onglet ? "page" : undefined}
            className={tabClasses(o.id === onglet)}
          >
            {o.label}
          </Link>
        ))}
      </Tabs>
      {onglet === "overview" && (
        <>
          {a.description && <p className="max-w-3xl text-sm text-ink-muted">{a.description}</p>}
          <Statut org={org} agent={a} />
          <Card title="Lifecycle">
            <LifecyclePanel org={org} agent={a} />
          </Card>
          <Mesures org={org} slug={slug} />
        </>
      )}
      {onglet === "definition" && <Definition org={org} agent={a} />}
      {onglet === "versions" && <Versions agent={a} />}
      {onglet === "runs" && <Runs org={org} slug={slug} />}
      {onglet === "access" && (
        <>
          <Acces agent={a} />
          {a.kind === "external" && <Clients org={org} slug={slug} />}
        </>
      )}
    </div>
  );
}

/** Suspendre, réactiver, révoquer : la révocation se relit à chaque appel (S18-06). */
function Statut({ org, agent }: { org: string; agent: Agent }) {
  const client = useQueryClient();
  const [erreur, setErreur] = useState<string | null>(null);
  const [aConfirmer, setAConfirmer] = useState(false);
  async function passer(status: "active" | "suspended" | "revoked") {
    setErreur(null);
    try {
      await api.updateAgent(org, agent.slug, { status });
      setAConfirmer(false);
      await client.invalidateQueries({ queryKey: ["agent", org, agent.slug] });
      await client.invalidateQueries({ queryKey: ["agents", org] });
    } catch (cause) {
      setErreur(cause instanceof Error ? cause.message : "refused");
    }
  }
  if (agent.status === "revoked") return null;
  return (
    <div className="flex flex-wrap items-center gap-2">
      {agent.status === "active" ? (
        <Button size="sm" onClick={() => void passer("suspended")}>
          Suspend
        </Button>
      ) : (
        <Button size="sm" onClick={() => void passer("active")}>
          Reactivate
        </Button>
      )}
      {aConfirmer ? (
        <span className="inline-flex items-center gap-2 text-sm">
          Revoked for good: its next call is refused.
          <Button size="sm" tone="danger" onClick={() => void passer("revoked")}>
            Revoke
          </Button>
          <Button size="sm" onClick={() => setAConfirmer(false)}>
            Cancel
          </Button>
        </span>
      ) : (
        <Button size="sm" tone="danger" onClick={() => setAConfirmer(true)}>
          Revoke…
        </Button>
      )}
      {erreur && <ErrorNote>{erreur}</ErrorNote>}
    </div>
  );
}

/** Ses runs sur 30 jours, leur issue, leur coût — modèles et outils —, par projet ; sa journée. */
function Mesures({ org, slug }: { org: string; slug: string }) {
  const mesures = useQuery({ queryKey: ["agent-metrics", org, slug], queryFn: () => api.agentMetrics(org, slug) });
  if (mesures.error) return <ErrorNote>{String(mesures.error)}</ErrorNote>;
  const m = mesures.data;
  return (
    <Card title="Last 30 days">
      {!m ? (
        <p className="text-sm text-ink-muted">Reading…</p>
      ) : (
        <div className="space-y-3" data-testid="mesures">
          <dl className="grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
            <div>
              <dt className="text-xs text-ink-muted">Runs</dt>
              <dd>
                {m.runs} ({m.succeeded} succeeded, {m.failed} failed)
              </dd>
            </div>
            <div>
              <dt className="text-xs text-ink-muted">Success rate</dt>
              <dd>{m.success_rate == null ? "—" : percent(m.success_rate)}</dd>
            </div>
            <div>
              <dt className="text-xs text-ink-muted">Cost</dt>
              <dd>
                {usd(m.cost_usd ?? 0)}
                {Object.entries(m.cost_by_kind ?? {}).length > 0 && (
                  <span className="ml-1 text-xs text-ink-muted">
                    (
                    {Object.entries(m.cost_by_kind ?? {})
                      .map(([sorte, cout]) => `${sorte} ${usd(cout)}`)
                      .join(", ")}
                    )
                  </span>
                )}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-ink-muted">Today</dt>
              <dd>
                {usd(m.spent_today_usd ?? 0)}
                {m.daily_budget_usd != null && ` of ${usd(m.daily_budget_usd)}`}
              </dd>
            </div>
          </dl>
          {(m.by_project ?? []).length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-xs text-ink-muted">
                  <tr>
                    <th className="py-1 font-normal">Project</th>
                    <th className="py-1 font-normal">Runs</th>
                    <th className="py-1 font-normal">Cost</th>
                  </tr>
                </thead>
                <tbody>
                  {(m.by_project ?? []).map((ligne) => (
                    <tr key={ligne.project} className="border-t border-line">
                      <td className="py-1">{ligne.project}</td>
                      <td>{ligne.runs}</td>
                      <td>{usd(ligne.cost_usd)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

/** What the agent is: its current version, and the next one, published from it (S30-05). */
function Definition({ org, agent }: { org: string; agent: Agent }) {
  const versions = [...(agent.versions ?? [])].sort((a, b) => b.version - a.version);
  const courante = versions[0];
  // The publication message lives HERE: the form remounts when the published version arrives
  // (key `latest_version`), and used to take the message with it (S23-13, seen in CI).
  const [publiee, setPubliee] = useState<string | null>(null);
  return (
    <>
      <Card title={`Version ${courante?.version ?? "—"}`}>
        {courante ? <Specification spec={courante.spec} /> : <p className="text-sm text-ink-muted">No version</p>}
      </Card>
      {agent.status !== "revoked" && (
        // No card around the builder: it lays out its own two columns, and a card's padding took the width (S39-05).
        <section className="space-y-3" aria-labelledby="new-version-title">
          <Heading as="h2" size="md" id="new-version-title">{`New version (v${agent.latest_version + 1})`}</Heading>
          <AgentBuilder
            key={agent.latest_version}
            org={org}
            mode={{ kind: "version", agent }}
            start={courante?.spec ?? {}}
            onDone={setPubliee}
          />
        </section>
      )}
      {publiee && (
        <p className="text-sm" role="status">
          {publiee}
        </p>
      )}
    </>
  );
}

/** Les versions : immuables. One is read at a time; the newest by default. */
function Versions({ agent }: { agent: Agent }) {
  const versions = [...(agent.versions ?? [])].sort((a, b) => b.version - a.version);
  const [choix, setChoix] = useState<number | null>(null);
  const choisie = versions.find((v) => v.version === (choix ?? agent.latest_version)) ?? versions[0];
  if (versions.length === 0) return <Empty>No version</Empty>;
  return (
    <>
      <Card title="Versions" padding="none" className="overflow-hidden">
        <Table aria-label="Versions">
          <THead>
            <TR>
              <TH>Version</TH>
              <TH>Published by</TH>
              <TH>When</TH>
              <TH>Checksum</TH>
              <TH>
                <span className="sr-only">Actions</span>
              </TH>
            </TR>
          </THead>
          <TBody>
            {versions.map((v) => (
              <TR key={v.version} aria-current={v.version === choisie?.version ? "true" : undefined}>
                <TD className="font-mono">v{v.version}</TD>
                <TD>{v.created_by ?? "—"}</TD>
                <TD className="font-mono text-xs">{v.created_at ? shortDate(v.created_at) : "—"}</TD>
                <TD className="font-mono text-xs">{v.checksum.slice(0, 19)}</TD>
                <TD align="right">
                  <Button size="sm" onClick={() => setChoix(v.version)} disabled={v.version === choisie?.version}>
                    View<span className="sr-only"> v{v.version}</span>
                  </Button>
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      </Card>
      {choisie && (
        <Card title={`Version ${choisie.version}`}>
          <Specification spec={choisie.spec} />
          <p className="mt-3 text-xs text-ink-muted">
            {choisie.created_by ? `Published by ${choisie.created_by}` : "Published"}
            {choisie.created_at ? ` ${relative(choisie.created_at)}` : ""} · {choisie.checksum.slice(0, 19)}
          </p>
        </Card>
      )}
    </>
  );
}

/** Its latest runs, across the projects one may read (S30-05): one request. */
function Runs({ org, slug }: { org: string; slug: string }) {
  const runs = useQuery({ queryKey: ["agent-runs", org, slug], queryFn: () => api.agentRuns(org, slug) });
  if (!runs.data) return <EtatDeLecture lecture={runs} quoi="the agent's runs" />;
  if (runs.data.length === 0) return <Empty title="No run yet">A workflow that names this agent runs it.</Empty>;
  return (
    <Card title="Latest runs" padding="none" className="overflow-hidden">
      <Table aria-label="Runs" data-testid="runs-de-l-agent">
        <THead>
          <TR>
            <TH>Started</TH>
            <TH>Project</TH>
            <TH>Stage</TH>
            <TH>Status</TH>
            <TH align="right">Cost</TH>
          </TR>
        </THead>
        <TBody>
          {runs.data.map((run) => (
            <TR key={run.id} interactive>
              <TD className="font-mono text-xs">{run.started_at ? shortDate(run.started_at) : "—"}</TD>
              <TD>
                <span data-donnee>{run.project_slug}</span>
              </TD>
              <TD>
                <Link href={`/p/${run.project_slug}/runs/${run.id}`} className="hover:underline">
                  {run.stage_role} · attempt {run.attempt}
                </Link>
              </TD>
              <TD>
                <StateBadge state={run.status} kind={run.status === "succeeded" ? "terminal" : run.status === "failed" ? "blocked" : "work"} />
              </TD>
              <TD align="right" className="font-mono">
                {usd(run.cost_usd ?? 0)}
              </TD>
            </TR>
          ))}
        </TBody>
      </Table>
    </Card>
  );
}

/** What the agent may reach: the tools and servers its current version names, and its skills. */
function Acces({ agent }: { agent: Agent }) {
  const courante = [...(agent.versions ?? [])].sort((a, b) => b.version - a.version)[0];
  const serveurs = courante?.spec.mcp_servers ?? [];
  return (
    <Card title="Tools and servers">
      {serveurs.length === 0 ? (
        <p className="text-sm text-ink-muted">
          Its version names no server: it reaches what the platform gives a run of its project
          {agent.kind === "external" ? ", within its human's rights" : ""}.
        </p>
      ) : (
        <ul className="space-y-1 text-sm" data-testid="acces-de-l-agent">
          {serveurs.map((s) => (
            <li key={s.connector}>
              <span className="font-mono">{s.connector}</span>
              <span className="text-ink-muted"> — {(s.tools ?? []).length ? (s.tools ?? []).join(", ") : "every tool"}</span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function Specification({ spec }: { spec: AgentSpec }) {
  const outils = (spec.mcp_servers ?? []).flatMap((s) => (s.tools ?? []).map((t) => `${s.connector}: ${t}`));
  return (
    <div className="space-y-3 text-sm" data-testid="version">
      <p className="text-ink-muted">{resumeDeLaVersion(spec)}</p>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-xs">
        <dt className="text-ink-muted">Model</dt>
        <dd>{spec.model ?? "The project's profile"}</dd>
        <dt className="text-ink-muted">Backend</dt>
        <dd>{spec.backend ?? "The project's"}</dd>
        <dt className="text-ink-muted">Limits</dt>
        <dd>
          {spec.limits?.max_turns ?? "—"} turns · {spec.limits?.max_minutes ?? "—"} min
        </dd>
        <dt className="text-ink-muted">Budget</dt>
        <dd>
          {spec.budget?.run_usd != null ? `${usd(spec.budget.run_usd)}/run` : "—"} ·{" "}
          {spec.budget?.daily_usd != null ? `${usd(spec.budget.daily_usd)}/day` : "—"}
          {spec.budget?.monthly_usd != null ? ` · ${usd(spec.budget.monthly_usd)}/month` : ""}
        </dd>
        <dt className="text-ink-muted">Skills</dt>
        <dd>
          {(spec.skills ?? []).length === 0
            ? "None"
            : (spec.skills ?? []).map((s) => (
                <Link key={s.slug} href={`/skills/${s.slug}`} className="mr-2 underline">
                  {s.slug}
                  {s.version ? `@${s.version}` : ""}
                </Link>
              ))}
        </dd>
        <dt className="text-ink-muted">Tools</dt>
        <dd>{outils.length === 0 ? "Those of the platform (or of its human)" : outils.join(", ")}</dd>
      </dl>
      {spec.instructions ? (
        <pre aria-label="Instructions" className="whitespace-pre-wrap break-words rounded bg-surface-muted p-3 text-xs">
          {spec.instructions}
        </pre>
      ) : (
        <p className="text-xs text-ink-muted">No instructions: the playbook of its role speaks.</p>
      )}
    </div>
  );
}

/** Les clients MCP qui incarnent cet agent : quand chacun a appelé, avec quoi ; en rattacher, détacher. */
function Clients({ org, slug }: { org: string; slug: string }) {
  const client = useQueryClient();
  const liens = useQuery({
    queryKey: ["agent-credentials", org, slug],
    queryFn: () => api.agentCredentials(org, slug),
  });
  const jetons = useQuery({ queryKey: ["tokens"], queryFn: () => api.myTokens() });
  const [choisi, setChoisi] = useState("");
  const [erreur, setErreur] = useState<string | null>(null);
  const rattaches = new Set((liens.data ?? []).map((l) => l.token_id));
  const libres = (jetons.data ?? []).filter((j) => estUnClientMcp(j) && !rattaches.has(j.id));
  async function agir(geste: () => Promise<unknown>) {
    setErreur(null);
    try {
      await geste();
      setChoisi("");
      await client.invalidateQueries({ queryKey: ["agent-credentials", org, slug] });
    } catch (cause) {
      setErreur(cause instanceof Error ? cause.message : "refused");
    }
  }
  return (
    <Card title="MCP clients acting as this agent">
      <div className="space-y-3">
        {(liens.data ?? []).length === 0 ? (
          <p className="text-sm text-ink-muted">No client yet: attach one of your MCP tokens below.</p>
        ) : (
          <ul className="space-y-2" data-testid="clients-de-l-agent">
            {(liens.data ?? []).map((lien) => {
              const etat = etatDuClient(lien);
              return (
                <li key={lien.id} className="flex flex-wrap items-center gap-3 text-sm">
                  <span className="font-medium">{lien.token_name ?? lien.client_id ?? lien.kind}</span>
                  <Badge tone={etat.connecte ? (etat.recent ? "ok" : "neutral") : "neutral"}>
                    {etat.connecte ? "Connected" : "Never called"}
                  </Badge>
                  <span className="text-xs text-ink-muted">
                    {lien.last_used_at ? `Last call ${relative(lien.last_used_at)}` : lien.kind}
                    {lien.last_client ? ` · ${lien.last_client}` : ""}
                    {lien.created_by ? ` · attached by ${lien.created_by}` : ""}
                  </span>
                  <span className="ml-auto">
                    <Button size="sm" onClick={() => void agir(() => api.detachAgentCredential(org, slug, lien.id))}>
                      Detach
                    </Button>
                  </span>
                </li>
              );
            })}
          </ul>
        )}
        {libres.length > 0 && (
          <form
            className="flex flex-wrap items-end gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              void agir(() => api.attachAgentToken(org, slug, choisi));
            }}
          >
            <label className="space-y-1">
              <span className="text-xs text-ink-muted">Attach one of your MCP tokens</span>
              <Select value={choisi} onChange={(e) => setChoisi(e.target.value)}>
                <option value="">—</option>
                {libres.map((j) => (
                  <option key={j.id} value={j.id}>
                    {j.name}
                  </option>
                ))}
              </Select>
            </label>
            <Button size="sm" type="submit" disabled={!choisi}>
              Attach
            </Button>
          </form>
        )}
        {erreur && <ErrorNote>{erreur}</ErrorNote>}
      </div>
    </Card>
  );
}
