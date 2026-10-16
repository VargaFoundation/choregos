// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { use, useState } from "react";
import {
  implicitFromApi,
  resumeDeLaVersion,
  slugDeLActeur,
  versionDeLActeur,
  type AgentImplicite,
} from "@/components/agents/registre";
import { ScopeBadge } from "@/components/agents/governance";
import { Badge, Button, Card, Empty, ErrorNote, Input, Select, buttonClasses } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { Agent, ProjectAgent } from "@/lib/types";
import { GesteConfirme } from "@/components/geste-confirme";

/**
 * Les agents d'un projet : ceux qu'il épingle — une version du registre, que ses surcharges ne
 * peuvent que resserrer — et les agents implicites de ses workflows, à enregistrer.
 */
export default function ProjectAgentsPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const { org } = useSession();
  const epingles = useQuery({ queryKey: ["project-agents", slug], queryFn: () => api.projectAgents(slug) });
  const registre = useQuery({ queryKey: ["agents", org], queryFn: () => api.agents(org) });
  // A project's own agents come first; a personal agent, or another project's, cannot be pinned here (ADR 0061).
  const own = (registre.data ?? []).filter((a) => a.scope === "project" && a.project === slug);
  const pinnable = (registre.data ?? []).filter(
    (a) => a.status === "active" && a.scope !== "personal" && !(a.scope === "project" && a.project !== slug),
  );
  return (
    <div className="space-y-6">
      <ProjectChannel projet={slug} />
      <Card title="Project agents">
        <div className="space-y-3">
          <p className="text-sm text-ink-muted">
            Agents this project owns: its owner changes them, and they run only in its workflows.
          </p>
          {own.length === 0 ? (
            <p className="text-sm text-ink-muted" data-testid="project-owned-none">
              None yet. Fork a common agent into this project, or write one.
            </p>
          ) : (
            <ul className="space-y-2" data-testid="project-owned">
              {own.map((a) => (
                <li key={a.slug} className="flex flex-wrap items-center gap-3 text-sm">
                  <Link href={`/agents/${a.slug}`} className="font-medium hover:underline">
                    {a.display_name}
                  </Link>
                  <ScopeBadge agent={a} />
                  <Badge tone="neutral">v{a.latest_version}</Badge>
                  {a.forked_from && (
                    <span className="text-xs text-ink-muted">
                      forked from {a.forked_from.slug} v{a.forked_from.version}
                      {a.forked_from.behind ? " (behind)" : ""}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
          <Link href={`/agents/new?scope=project&project=${slug}`} className={buttonClasses("secondary", "sm")}>
            New project agent
          </Link>
        </div>
      </Card>
      <Card title="Pinned agents">
        {epingles.error ? (
          <ErrorNote>{String(epingles.error)}</ErrorNote>
        ) : !epingles.data ? (
          <p className="text-sm text-ink-muted">Reading…</p>
        ) : epingles.data.length === 0 ? (
          <Empty title="Nothing pinned">
            A workflow actor that names an agent runs its production version; pin one to hold a revision or follow a
            channel, and tighten it.
          </Empty>
        ) : (
          <ul className="space-y-3" data-testid="epingles">
            {epingles.data.map((epingle) => (
              <Epingle key={epingle.agent} projet={slug} epingle={epingle} />
            ))}
          </ul>
        )}
      </Card>
      <Epingler projet={slug} agents={pinnable} />
      <Implicites projet={slug} org={org} registre={registre.data ?? []} />
    </div>
  );
}

/** Which channel of the agents the project runs when it pins nothing (S40-02, ADR 0071). */
function ProjectChannel({ projet }: { projet: string }) {
  const client = useQueryClient();
  const lu = useQuery({ queryKey: ["project-agent-channel", projet], queryFn: () => api.projectAgentChannel(projet), retry: false });
  const [refuse, setRefuse] = useState<string | null>(null);
  const [erreur, setErreur] = useState<string | null>(null);
  async function changer(channel: "stable" | "candidate") {
    setErreur(null);
    try {
      await api.setProjectAgentChannel(projet, channel);
      await client.invalidateQueries({ queryKey: ["project-agent-channel", projet] });
      await client.invalidateQueries({ queryKey: ["agent-exposure"] });
    } catch (cause) {
      // The API's sentence stays, and the control is held back: the person cannot do this here.
      if (cause instanceof ApiError && cause.status === 403) setRefuse(cause.message);
      else setErreur(cause instanceof Error ? cause.message : "refused");
    }
  }
  if (lu.error) return null;
  const canal = lu.data?.channel ?? "stable";
  return (
    <Card title="Which versions this project runs">
      <div className="space-y-2 text-sm" data-testid="project-channel">
        <label className="flex flex-wrap items-center gap-2">
          <span>This project runs:</span>
          <Select
            aria-label="Versions this project runs"
            value={canal}
            disabled={!lu.data || refuse != null}
            onChange={(e) => void changer(e.target.value as "stable" | "candidate")}
          >
            <option value="stable">Production versions</option>
            <option value="candidate">Testing versions</option>
          </Select>
        </label>
        <p className="text-xs text-ink-muted">
          {canal === "candidate"
            ? "Every agent that has a revision in testing runs it here. Agents with nothing in testing run production. A fixed pin below still wins."
            : "Every agent runs its production revision here, unless pinned below. A share of work items may still run an agent's testing revision (its canary)."}
        </p>
        {refuse && (
          <p className="text-xs text-ink-muted" data-testid="project-channel-refused">
            {refuse}
          </p>
        )}
        {erreur && <ErrorNote>{erreur}</ErrorNote>}
      </div>
    </Card>
  );
}

function Epingle({ projet, epingle }: { projet: string; epingle: ProjectAgent }) {
  const client = useQueryClient();
  const resserre = Object.values(epingle.overrides ?? {}).some((valeur) => valeur != null);
  return (
    <li className="flex flex-wrap items-center gap-3 text-sm">
      <Link href={`/agents/${epingle.agent}`} className="font-medium hover:underline">
        {epingle.agent}
      </Link>
      <Badge tone="neutral">v{epingle.version}</Badge>
      <Badge tone={epingle.channel ? "agent" : "neutral"} data-testid="pin-mode">
        {epingle.channel === "candidate" ? "follows testing" : epingle.channel === "stable" ? "follows production" : "fixed revision"}
      </Badge>
      {resserre && <Badge tone="warn">Tightened</Badge>}
      <span className="text-xs text-ink-muted">{resumeDeLaVersion(epingle.effective)}</span>
      <span className="ml-auto">
        <GesteConfirme
          question={`Unpin ${epingle.agent}? Its workflows run its production version from now on${resserre ? ", and the limits tightened here are lost" : ""}.`}
          confirmer={`Unpin ${epingle.agent}`}
          action={async () => {
            await api.unpinProjectAgent(projet, epingle.agent);
            await client.invalidateQueries({ queryKey: ["project-agents", projet] });
          }}
        >
          Unpin
        </GesteConfirme>
      </span>
    </li>
  );
}

/** Épingler une version ; ses limites et budgets ne se changent qu'à la baisse (l'API le refuse sinon). */
function Epingler({ projet, agents }: { projet: string; agents: Agent[] }) {
  const client = useQueryClient();
  const [agent, setAgent] = useState("");
  const [version, setVersion] = useState("");
  const [mode, setMode] = useState<"stable" | "candidate" | "fixed">("stable");
  const [quotidien, setQuotidien] = useState("");
  const [tours, setTours] = useState("");
  const [erreur, setErreur] = useState<string | null>(null);
  const choisi = agents.find((a) => a.slug === agent);
  async function epingler() {
    setErreur(null);
    try {
      await api.pinProjectAgent(projet, agent, {
        ...(mode === "fixed" ? { version: Number(version || choisi?.latest_version || 1) } : { channel: mode }),
        overrides: {
          ...(quotidien ? { budget: { daily_usd: Number(quotidien) } } : {}),
          ...(tours ? { limits: { max_turns: Number(tours) } } : {}),
        },
      });
      setAgent("");
      setVersion("");
      setMode("stable");
      setQuotidien("");
      setTours("");
      await client.invalidateQueries({ queryKey: ["project-agents", projet] });
    } catch (cause) {
      setErreur(cause instanceof Error ? cause.message : "pin refused");
    }
  }
  if (agents.length === 0) return null;
  return (
    <Card title="Pin an agent">
      <form
        className="grid gap-3 md:grid-cols-4"
        onSubmit={(event) => {
          event.preventDefault();
          void epingler();
        }}
      >
        <label className="space-y-1">
          <span className="text-xs text-ink-muted">Agent</span>
          <Select
            value={agent}
            onChange={(e) => {
              setAgent(e.target.value);
              setVersion("");
            }}
          >
            <option value="">—</option>
            {agents.map((a) => (
              <option key={a.slug} value={a.slug}>
                {a.display_name}
              </option>
            ))}
          </Select>
        </label>
        <label className="space-y-1">
          <span className="text-xs text-ink-muted">Follow</span>
          <Select value={mode} onChange={(e) => setMode(e.target.value as typeof mode)} disabled={!choisi}>
            <option value="stable">Follow production</option>
            <option value="candidate">Follow testing</option>
            <option value="fixed">Fixed revision</option>
          </Select>
        </label>
        <label className="space-y-1">
          <span className="text-xs text-ink-muted">Revision</span>
          <Select value={version} onChange={(e) => setVersion(e.target.value)} disabled={!choisi || mode !== "fixed"}>
            {Array.from({ length: choisi?.latest_version ?? 0 }, (_, i) => (choisi?.latest_version ?? 0) - i).map((v) => (
              <option key={v} value={v}>
                v{v}
              </option>
            ))}
          </Select>
        </label>
        <label className="space-y-1">
          <span className="text-xs text-ink-muted">Daily budget, lower (USD)</span>
          <Input type="number" min={0} step="0.5" value={quotidien} onChange={(e) => setQuotidien(e.target.value)} />
        </label>
        <label className="space-y-1">
          <span className="text-xs text-ink-muted">Max turns, lower</span>
          <Input type="number" min={1} value={tours} onChange={(e) => setTours(e.target.value)} />
        </label>
        <div className="md:col-span-4">
          <Button type="submit" size="sm" disabled={!agent}>
            Pin
          </Button>
        </div>
        {erreur && (
          <div className="md:col-span-4">
            <ErrorNote>{erreur}</ErrorNote>
          </div>
        )}
      </form>
    </Card>
  );
}

/** Les acteurs `agent` des workflows du projet qui ne nomment aucun agent du registre. */
function Implicites({ projet, org, registre }: { projet: string; org: string; registre: Agent[] }) {
  // One request: the API reads the active workflows (S30-04) — it was one request per workflow.
  const lecture = useQuery({ queryKey: ["implicit-agents", projet], queryFn: () => api.implicitAgents(projet) });
  const implicites = implicitFromApi(lecture.data ?? []);
  return (
    <Card title="Implicit agents">
      <div className="space-y-3">
        <p className="text-sm text-ink-muted">
          Workflow actors that run as agents without naming one: no editable instructions, no versions, no costs of
          their own. Register one, then name it in the workflow (<code>agent: its-slug</code>).
        </p>
        {implicites.length === 0 ? (
          <p className="text-sm text-ink-muted">None: every agent of this project&apos;s workflows is in the registry.</p>
        ) : (
          <ul className="space-y-2" data-testid="implicites">
            {implicites.map((implicite) => (
              <Implicite key={`${implicite.workflow}/${implicite.acteur}`} org={org} implicite={implicite} pris={registre} />
            ))}
          </ul>
        )}
      </div>
    </Card>
  );
}

function Implicite({ org, implicite, pris }: { org: string; implicite: AgentImplicite; pris: Agent[] }) {
  const client = useQueryClient();
  const [fait, setFait] = useState<string | null>(null);
  const [erreur, setErreur] = useState<string | null>(null);
  const slug = slugDeLActeur(implicite.acteur);
  const deja = pris.some((a) => a.slug === slug);
  async function enregistrer() {
    setErreur(null);
    try {
      await api.createAgent(org, {
        slug,
        kind: "internal",
        display_name: implicite.acteur,
        description: `Registered from the ${implicite.workflow} workflow${implicite.role ? ` (role ${implicite.role})` : ""}`,
        spec: versionDeLActeur(implicite),
      });
      setFait(`Registered: name it in ${implicite.workflow} — actors.${implicite.acteur}.agent: ${slug}`);
      await client.invalidateQueries({ queryKey: ["agents", org] });
    } catch (cause) {
      setErreur(cause instanceof Error ? cause.message : "registration refused");
    }
  }
  return (
    <li className="flex flex-wrap items-center gap-3 text-sm">
      <span className="font-medium" data-donnee>
        {implicite.acteur}
      </span>
      <span className="text-xs text-ink-muted" data-donnee>
        {implicite.workflow}
        {implicite.role ? ` · role ${implicite.role}` : ""}
        {implicite.model ? ` · ${implicite.model}` : ""}
      </span>
      <span className="ml-auto">
        {fait ? (
          <span role="status" className="text-xs">
            {fait}
          </span>
        ) : deja ? (
          <span className="text-xs text-ink-muted">
            <Link href={`/agents/${slug}`} className="underline">
              {slug}
            </Link>{" "}
            exists: name it in the workflow
          </span>
        ) : (
          <Button size="sm" onClick={() => void enregistrer()}>
            Register
          </Button>
        )}
      </span>
      {erreur && <ErrorNote>{erreur}</ErrorNote>}
    </li>
  );
}
