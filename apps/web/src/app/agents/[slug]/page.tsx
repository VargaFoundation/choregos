// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { use, useState } from "react";
import { Badge, Heading } from "@varga/design-system";
import { estUnClientMcp, etatDuClient, resumeDeLaVersion } from "@/components/agents/registre";
import { Button, Card, Empty, ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";
import { percent, relative, shortDate, usd } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { Agent, AgentSpec } from "@/lib/types";

const champ = "w-full rounded border border-line bg-surface px-2 py-1.5 text-sm";

/** Un agent du registre : ce qu'il a fait et coûté, ses versions, et — s'il est externe — ses clients. */
export default function AgentPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const { org } = useSession();
  const agent = useQuery({ queryKey: ["agent", org, slug], queryFn: () => api.agent(org, slug) });
  if (agent.error) return <ErrorNote>{String(agent.error)}</ErrorNote>;
  if (!agent.data) return <Empty>reading the agent…</Empty>;
  const a = agent.data;
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-baseline gap-3">
        <Link href="/agents" className="text-sm text-ink-muted hover:underline">
          agents
        </Link>
        <span aria-hidden className="text-ink-muted">
          /
        </span>
        <Heading as="h1" size="lg">
          {a.display_name}
        </Heading>
        <span className="text-sm text-ink-muted">{a.slug}</span>
        <Badge tone={a.kind === "external" ? "accent" : "neutral"}>{a.kind}</Badge>
        <Badge tone={a.status === "active" ? "ok" : "danger"}>{a.status}</Badge>
        {a.owner && <span className="text-xs text-ink-muted">owned by {a.owner}</span>}
        {a.expires_at && <span className="text-xs text-ink-muted">expires {shortDate(a.expires_at)}</span>}
      </div>
      {a.description && <p className="max-w-3xl text-sm text-ink-muted">{a.description}</p>}
      <Statut org={org} agent={a} />
      <Mesures org={org} slug={slug} />
      <Versions org={org} agent={a} />
      {a.kind === "external" && <Clients org={org} slug={slug} />}
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
          suspend
        </Button>
      ) : (
        <Button size="sm" onClick={() => void passer("active")}>
          reactivate
        </Button>
      )}
      {aConfirmer ? (
        <span className="inline-flex items-center gap-2 text-sm">
          revoked for good: its next call is refused.
          <Button size="sm" tone="danger" onClick={() => void passer("revoked")}>
            revoke
          </Button>
          <Button size="sm" onClick={() => setAConfirmer(false)}>
            cancel
          </Button>
        </span>
      ) : (
        <Button size="sm" tone="danger" onClick={() => setAConfirmer(true)}>
          revoke…
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
    <Card title="last 30 days">
      {!m ? (
        <p className="text-sm text-ink-muted">reading…</p>
      ) : (
        <div className="space-y-3" data-testid="mesures">
          <dl className="grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
            <div>
              <dt className="text-xs text-ink-muted">runs</dt>
              <dd>
                {m.runs} ({m.succeeded} succeeded, {m.failed} failed)
              </dd>
            </div>
            <div>
              <dt className="text-xs text-ink-muted">success rate</dt>
              <dd>{m.success_rate == null ? "—" : percent(m.success_rate)}</dd>
            </div>
            <div>
              <dt className="text-xs text-ink-muted">cost</dt>
              <dd>
                {usd(m.cost_usd ?? 0)}
                {Object.entries(m.cost_by_kind ?? {}).length > 0 && (
                  <span className="ml-1 text-xs text-ink-muted">
                    ({Object.entries(m.cost_by_kind ?? {})
                      .map(([sorte, cout]) => `${sorte} ${usd(cout)}`)
                      .join(", ")})
                  </span>
                )}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-ink-muted">today</dt>
              <dd>
                {usd(m.spent_today_usd ?? 0)}
                {m.daily_budget_usd != null && ` of ${usd(m.daily_budget_usd)}`}
              </dd>
            </div>
          </dl>
          {(m.by_project ?? []).length > 0 && (
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-ink-muted">
                <tr>
                  <th className="py-1 font-normal">project</th>
                  <th className="py-1 font-normal">runs</th>
                  <th className="py-1 font-normal">cost</th>
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
          )}
        </div>
      )}
    </Card>
  );
}

/** Les versions : immuables. En publier une autre, c'est partir de la dernière et la changer. */
function Versions({ org, agent }: { org: string; agent: Agent }) {
  const versions = [...(agent.versions ?? [])].sort((a, b) => b.version - a.version);
  const [choisie, setChoisie] = useState(agent.latest_version);
  const version = versions.find((v) => v.version === choisie) ?? versions[0];
  return (
    <>
      <Card
        title={`version ${version?.version ?? "—"}`}
        action={
          versions.length > 1 ? (
            <label className="flex items-center gap-2 text-xs">
              <span className="text-ink-muted">see</span>
              <select
                aria-label="version to see"
                className="rounded border border-line bg-surface px-2 py-1"
                value={choisie}
                onChange={(e) => setChoisie(Number(e.target.value))}
              >
                {versions.map((v) => (
                  <option key={v.version} value={v.version}>
                    v{v.version}
                  </option>
                ))}
              </select>
            </label>
          ) : undefined
        }
      >
        {version ? <Specification spec={version.spec} /> : <p className="text-sm text-ink-muted">no version</p>}
        {version && (
          <p className="mt-3 text-xs text-ink-muted">
            {version.created_by ? `published by ${version.created_by}` : "published"}
            {version.created_at ? ` ${relative(version.created_at)}` : ""} · {version.checksum.slice(0, 19)}
          </p>
        )}
      </Card>
      {agent.status !== "revoked" && (
        <NouvelleVersion key={agent.latest_version} org={org} agent={agent} depart={versions[0]?.spec ?? {}} />
      )}
    </>
  );
}

function Specification({ spec }: { spec: AgentSpec }) {
  const outils = (spec.mcp_servers ?? []).flatMap((s) => (s.tools ?? []).map((t) => `${s.connector}: ${t}`));
  return (
    <div className="space-y-3 text-sm" data-testid="version">
      <p className="text-ink-muted">{resumeDeLaVersion(spec)}</p>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-xs">
        <dt className="text-ink-muted">model</dt>
        <dd>{spec.model ?? "the project's profile"}</dd>
        <dt className="text-ink-muted">backend</dt>
        <dd>{spec.backend ?? "the project's"}</dd>
        <dt className="text-ink-muted">limits</dt>
        <dd>
          {spec.limits?.max_turns ?? "—"} turns · {spec.limits?.max_minutes ?? "—"} min
        </dd>
        <dt className="text-ink-muted">budget</dt>
        <dd>
          {spec.budget?.run_usd != null ? `${usd(spec.budget.run_usd)}/run` : "—"} ·{" "}
          {spec.budget?.daily_usd != null ? `${usd(spec.budget.daily_usd)}/day` : "—"}
        </dd>
        <dt className="text-ink-muted">skills</dt>
        <dd>
          {(spec.skills ?? []).length === 0
            ? "none"
            : (spec.skills ?? []).map((s) => (
                <Link key={s.slug} href={`/skills/${s.slug}`} className="mr-2 underline">
                  {s.slug}
                  {s.version ? `@${s.version}` : ""}
                </Link>
              ))}
        </dd>
        <dt className="text-ink-muted">tools</dt>
        <dd>{outils.length === 0 ? "those of the platform (or of its human)" : outils.join(", ")}</dd>
      </dl>
      {spec.instructions ? (
        <pre aria-label="instructions" className="whitespace-pre-wrap break-words rounded bg-surface-muted p-3 text-xs">
          {spec.instructions}
        </pre>
      ) : (
        <p className="text-xs text-ink-muted">no instructions: the playbook of its role speaks.</p>
      )}
    </div>
  );
}

/** Une nouvelle version : la dernière, changée. Rien n'est réécrit ; le projet épinglé ne bouge pas. */
function NouvelleVersion({ org, agent, depart }: { org: string; agent: Agent; depart: AgentSpec }) {
  const client = useQueryClient();
  const [instructions, setInstructions] = useState(depart.instructions ?? "");
  const [modele, setModele] = useState(depart.model ?? "");
  const [tours, setTours] = useState(depart.limits?.max_turns?.toString() ?? "");
  const [quotidien, setQuotidien] = useState(depart.budget?.daily_usd?.toString() ?? "");
  const [erreur, setErreur] = useState<string | null>(null);
  const [fait, setFait] = useState<string | null>(null);
  async function publier() {
    setErreur(null);
    const spec: AgentSpec = {
      ...depart,
      instructions,
      model: modele || null,
      limits: { ...depart.limits, max_turns: tours ? Number(tours) : null },
      budget: { ...depart.budget, daily_usd: quotidien ? Number(quotidien) : null },
    };
    try {
      const publiee = await api.publishAgentVersion(org, agent.slug, spec);
      setFait(`v${publiee.version} published: projects keep the version they pinned`);
      await client.invalidateQueries({ queryKey: ["agent", org, agent.slug] });
    } catch (cause) {
      setErreur(cause instanceof Error ? cause.message : "publication refused");
    }
  }
  return (
    <Card title={`new version (v${agent.latest_version + 1})`}>
      <form
        className="grid gap-3 md:grid-cols-3"
        onSubmit={(event) => {
          event.preventDefault();
          void publier();
        }}
      >
        <label className="space-y-1 md:col-span-3">
          <span className="text-xs text-ink-muted">instructions</span>
          <textarea
            className={`${champ} font-mono text-xs`}
            rows={8}
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
          />
        </label>
        <label className="space-y-1">
          <span className="text-xs text-ink-muted">model</span>
          <input className={champ} value={modele} onChange={(e) => setModele(e.target.value)} placeholder="profile:standard" />
        </label>
        <label className="space-y-1">
          <span className="text-xs text-ink-muted">max turns</span>
          <input className={champ} type="number" min={1} value={tours} onChange={(e) => setTours(e.target.value)} />
        </label>
        <label className="space-y-1">
          <span className="text-xs text-ink-muted">daily budget (USD)</span>
          <input
            className={champ}
            type="number"
            min={0}
            step="0.5"
            value={quotidien}
            onChange={(e) => setQuotidien(e.target.value)}
          />
        </label>
        <div className="flex items-center gap-3 md:col-span-3">
          <Button type="submit" tone="accent">
            publish v{agent.latest_version + 1}
          </Button>
          {fait && (
            <span className="text-sm" role="status">
              {fait}
            </span>
          )}
        </div>
        {erreur && (
          <div className="md:col-span-3">
            <ErrorNote>{erreur}</ErrorNote>
          </div>
        )}
      </form>
    </Card>
  );
}

/** Les clients MCP qui incarnent cet agent : quand chacun a appelé, avec quoi ; en rattacher, détacher. */
function Clients({ org, slug }: { org: string; slug: string }) {
  const client = useQueryClient();
  const liens = useQuery({ queryKey: ["agent-credentials", org, slug], queryFn: () => api.agentCredentials(org, slug) });
  const jetons = useQuery({ queryKey: ["me-tokens"], queryFn: () => api.myTokens() });
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
          <p className="text-sm text-ink-muted">no client yet: attach one of your MCP tokens below.</p>
        ) : (
          <ul className="space-y-2" data-testid="clients-de-l-agent">
            {(liens.data ?? []).map((lien) => {
              const etat = etatDuClient(lien);
              return (
                <li key={lien.id} className="flex flex-wrap items-center gap-3 text-sm">
                  <span className="font-medium">{lien.token_name ?? lien.client_id ?? lien.kind}</span>
                  <Badge tone={etat.connecte ? (etat.recent ? "ok" : "neutral") : "neutral"}>
                    {etat.connecte ? "connected" : "never called"}
                  </Badge>
                  <span className="text-xs text-ink-muted">
                    {lien.last_used_at ? `last call ${relative(lien.last_used_at)}` : lien.kind}
                    {lien.last_client ? ` · ${lien.last_client}` : ""}
                    {lien.created_by ? ` · attached by ${lien.created_by}` : ""}
                  </span>
                  <span className="ml-auto">
                    <Button size="sm" onClick={() => void agir(() => api.detachAgentCredential(org, slug, lien.id))}>
                      detach
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
              <span className="text-xs text-ink-muted">attach one of your MCP tokens</span>
              <select className={champ} value={choisi} onChange={(e) => setChoisi(e.target.value)}>
                <option value="">—</option>
                {libres.map((j) => (
                  <option key={j.id} value={j.id}>
                    {j.name}
                  </option>
                ))}
              </select>
            </label>
            <Button size="sm" type="submit" disabled={!choisi}>
              attach
            </Button>
          </form>
        )}
        {erreur && <ErrorNote>{erreur}</ErrorNote>}
      </div>
    </Card>
  );
}
