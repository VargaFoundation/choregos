// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { use, useState } from "react";
import { Badge } from "@varga/design-system";
import {
  agentsImplicites,
  resumeDeLaVersion,
  slugDeLActeur,
  versionDeLActeur,
  type AgentImplicite,
} from "@/components/agents/registre";
import { Button, Card, Empty, ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { Agent, ProjectAgent } from "@/lib/types";

const champ = "w-full rounded border border-line bg-surface px-2 py-1.5 text-sm";

/**
 * Les agents d'un projet : ceux qu'il épingle — une version du registre, que ses surcharges ne
 * peuvent que resserrer — et les agents implicites de ses workflows, à enregistrer.
 */
export default function ProjectAgentsPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const { org } = useSession();
  const epingles = useQuery({ queryKey: ["project-agents", slug], queryFn: () => api.projectAgents(slug) });
  const registre = useQuery({ queryKey: ["agents", org], queryFn: () => api.agents(org) });
  return (
    <div className="space-y-6">
      <Card title="pinned agents">
        {epingles.error ? (
          <ErrorNote>{String(epingles.error)}</ErrorNote>
        ) : !epingles.data ? (
          <p className="text-sm text-ink-muted">reading…</p>
        ) : epingles.data.length === 0 ? (
          <Empty title="nothing pinned">
            a workflow actor that names an agent runs its latest version; pin one to hold it, and tighten it.
          </Empty>
        ) : (
          <ul className="space-y-3" data-testid="epingles">
            {epingles.data.map((epingle) => (
              <Epingle key={epingle.agent} projet={slug} epingle={epingle} />
            ))}
          </ul>
        )}
      </Card>
      <Epingler projet={slug} agents={(registre.data ?? []).filter((a) => a.status === "active")} />
      <Implicites projet={slug} org={org} registre={registre.data ?? []} />
    </div>
  );
}

function Epingle({ projet, epingle }: { projet: string; epingle: ProjectAgent }) {
  const client = useQueryClient();
  const [erreur, setErreur] = useState<string | null>(null);
  const resserre = Object.values(epingle.overrides ?? {}).some((valeur) => valeur != null);
  return (
    <li className="flex flex-wrap items-center gap-3 text-sm">
      <Link href={`/agents/${epingle.agent}`} className="font-medium hover:underline">
        {epingle.agent}
      </Link>
      <Badge tone="neutral">v{epingle.version}</Badge>
      {resserre && <Badge tone="warn">tightened</Badge>}
      <span className="text-xs text-ink-muted">{resumeDeLaVersion(epingle.effective)}</span>
      <span className="ml-auto">
        <Button
          size="sm"
          onClick={() =>
            void api
              .unpinProjectAgent(projet, epingle.agent)
              .then(() => client.invalidateQueries({ queryKey: ["project-agents", projet] }))
              .catch((cause: unknown) => setErreur(cause instanceof Error ? cause.message : "refused"))
          }
        >
          unpin
        </Button>
      </span>
      {erreur && <ErrorNote>{erreur}</ErrorNote>}
    </li>
  );
}

/** Épingler une version ; ses limites et budgets ne se changent qu'à la baisse (l'API le refuse sinon). */
function Epingler({ projet, agents }: { projet: string; agents: Agent[] }) {
  const client = useQueryClient();
  const [agent, setAgent] = useState("");
  const [version, setVersion] = useState("");
  const [quotidien, setQuotidien] = useState("");
  const [tours, setTours] = useState("");
  const [erreur, setErreur] = useState<string | null>(null);
  const choisi = agents.find((a) => a.slug === agent);
  async function epingler() {
    setErreur(null);
    try {
      await api.pinProjectAgent(projet, agent, {
        version: Number(version || choisi?.latest_version || 1),
        overrides: {
          ...(quotidien ? { budget: { daily_usd: Number(quotidien) } } : {}),
          ...(tours ? { limits: { max_turns: Number(tours) } } : {}),
        },
      });
      setAgent("");
      setVersion("");
      setQuotidien("");
      setTours("");
      await client.invalidateQueries({ queryKey: ["project-agents", projet] });
    } catch (cause) {
      setErreur(cause instanceof Error ? cause.message : "pin refused");
    }
  }
  if (agents.length === 0) return null;
  return (
    <Card title="pin an agent">
      <form
        className="grid gap-3 md:grid-cols-4"
        onSubmit={(event) => {
          event.preventDefault();
          void epingler();
        }}
      >
        <label className="space-y-1">
          <span className="text-xs text-ink-muted">agent</span>
          <select
            className={champ}
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
          </select>
        </label>
        <label className="space-y-1">
          <span className="text-xs text-ink-muted">version</span>
          <select className={champ} value={version} onChange={(e) => setVersion(e.target.value)} disabled={!choisi}>
            {Array.from({ length: choisi?.latest_version ?? 0 }, (_, i) => (choisi?.latest_version ?? 0) - i).map((v) => (
              <option key={v} value={v}>
                v{v}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1">
          <span className="text-xs text-ink-muted">daily budget, lower (USD)</span>
          <input className={champ} type="number" min={0} step="0.5" value={quotidien} onChange={(e) => setQuotidien(e.target.value)} />
        </label>
        <label className="space-y-1">
          <span className="text-xs text-ink-muted">max turns, lower</span>
          <input className={champ} type="number" min={1} value={tours} onChange={(e) => setTours(e.target.value)} />
        </label>
        <div className="md:col-span-4">
          <Button type="submit" size="sm" disabled={!agent}>
            pin
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
  const resumes = useQuery({ queryKey: ["workflows", projet], queryFn: () => api.workflows(projet) });
  const definitions = useQueries({
    queries: (resumes.data ?? []).map((w) => ({
      queryKey: ["workflow", projet, w.name],
      queryFn: () => api.workflowNamed(projet, w.name),
    })),
  });
  const workflows = definitions.flatMap((d) => (d.data ? [{ name: d.data.name, json: d.data.json }] : []));
  const implicites = agentsImplicites(workflows, new Set(registre.map((a) => a.slug)));
  return (
    <Card title="implicit agents">
      <div className="space-y-3">
        <p className="text-sm text-ink-muted">
          Workflow actors that run as agents without naming one: no editable instructions, no versions, no costs of
          their own. Register one, then name it in the workflow (<code>agent: its-slug</code>).
        </p>
        {implicites.length === 0 ? (
          <p className="text-sm text-ink-muted">none: every agent of this project&apos;s workflows is in the registry.</p>
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
        description: `registered from the ${implicite.workflow} workflow${implicite.role ? ` (role ${implicite.role})` : ""}`,
        spec: versionDeLActeur(implicite),
      });
      setFait(`registered: name it in ${implicite.workflow} — actors.${implicite.acteur}.agent: ${slug}`);
      await client.invalidateQueries({ queryKey: ["agents", org] });
    } catch (cause) {
      setErreur(cause instanceof Error ? cause.message : "registration refused");
    }
  }
  return (
    <li className="flex flex-wrap items-center gap-3 text-sm">
      <span className="font-medium">{implicite.acteur}</span>
      <span className="text-xs text-ink-muted">
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
            register
          </Button>
        )}
      </span>
      {erreur && <ErrorNote>{erreur}</ErrorNote>}
    </li>
  );
}
