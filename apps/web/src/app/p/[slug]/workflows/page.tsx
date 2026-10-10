// SPDX-License-Identifier: Apache-2.0
"use client";

import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { use, useState } from "react";
import { GesteConfirme } from "@/components/geste-confirme";
import { Badge, Button, buttonClasses, Card, Empty, ErrorNote, EtatDeLecture, PhraseDuMoteur } from "@/components/ui";
import { api } from "@/lib/api";
import { shortDate } from "@/lib/format";
import type { WorkflowRouting, WorkflowSummary } from "@/lib/types";

type Regle = WorkflowRouting["rules"][number];

/** Les conditions d'une règle de routage, en clair : « étiqueté incident », « type Bug ». */
function condition(rule: Regle): string {
  const parts: string[] = [];
  if (rule.when.labels_any?.length) parts.push(`labelled ${rule.when.labels_any.join(" or ")}`);
  if (rule.when.labels_all?.length) parts.push(`labelled ${rule.when.labels_all.join(" and ")}`);
  if (rule.when.item_type) parts.push(`of type ${rule.when.item_type}`);
  return parts.join(", ") || "always";
}

/** « incident, sev1 » → ["incident", "sev1"] : des étiquettes, sans vide ni doublon. */
function etiquettes(texte: string): string[] {
  return [
    ...new Set(
      texte
        .split(",")
        .map((e) => e.trim())
        .filter(Boolean),
    ),
  ];
}

/**
 * Les workflows du projet (ADR 0031) : un projet en porte autant qu'il a de processus — l'arrivée et
 * le départ d'un projet RH, la livraison et le correctif d'une équipe produit. Chaque carte dit sa
 * version active, si c'est le défaut, ce qui y est routé et combien de tickets y sont épinglés.
 *
 * L'API savait déjà choisir le défaut, router par étiquette ou par type, et désactiver un workflow ;
 * la console ne savait que le dire (seconde passe du 08/10, S23-13). Ces trois gestes sont ici.
 */
export default function WorkflowsPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const client = useQueryClient();
  const workflows = useQuery({ queryKey: ["workflows", slug], queryFn: () => api.workflows(slug) });
  const routing = useQuery({ queryKey: ["workflow-routing", slug], queryFn: () => api.workflowRouting(slug) });

  async function relire() {
    await client.invalidateQueries({ queryKey: ["workflow-routing", slug] });
    await client.invalidateQueries({ queryKey: ["workflows", slug] });
  }

  if (workflows.error) return <ErrorNote>{String(workflows.error)}</ErrorNote>;
  const rules = routing.data?.rules ?? [];
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <p className="max-w-2xl text-sm text-ink-muted">
          Each workflow is a process of its own. A request is born in one — the one it names, else the first routing
          rule it matches, else the default — and stays pinned to that version until it ends.
        </p>
        <Link href={`/p/${slug}/workflows/new`} className={buttonClasses("secondary", "sm")}>
          new workflow
        </Link>
      </div>

      {workflows.data?.length === 0 && <Empty title="no workflow">Publish one from a template.</Empty>}
      <ul className="grid gap-4 md:grid-cols-2" aria-label="workflows">
        {workflows.data?.map((workflow) => (
          <li key={workflow.name}>
            <CarteDeWorkflow
              slug={slug}
              workflow={workflow}
              routing={routing.data}
              routed={rules.filter((rule) => rule.workflow === workflow.name)}
              onChange={relire}
            />
          </li>
        ))}
      </ul>

      <Card title="routing">
        {!routing.data || !workflows.data ? (
          <>
            <EtatDeLecture lecture={routing} quoi="the routing" />
            <EtatDeLecture lecture={workflows} quoi="the workflows" />
          </>
        ) : (
          <Routage
            slug={slug}
            routing={routing.data}
            noms={workflows.data.map((w) => w.name)}
            onChange={relire}
          />
        )}
      </Card>
    </div>
  );
}

function CarteDeWorkflow({
  slug,
  workflow,
  routing,
  routed,
  onChange,
}: {
  slug: string;
  workflow: WorkflowSummary;
  routing: WorkflowRouting | undefined;
  routed: Regle[];
  onChange: () => Promise<void>;
}) {
  const nom = workflow.name;
  // Ce que l'API refuserait, dit avant : le défaut et la cible d'une règle ne se désactivent pas.
  const pourquoiPas = workflow.is_default
    ? "the default cannot be deactivated — make another one the default first"
    : routed.length > 0
      ? "a routing rule sends requests here — remove it first"
      : null;
  return (
    <Card
      title={
        <Link href={`/p/${slug}/workflows/${encodeURIComponent(nom)}`} className="hover:underline">
          {nom}
        </Link>
      }
      action={workflow.is_default ? <Badge tone="accent">default</Badge> : undefined}
    >
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm" data-testid={`workflow-card-${nom}`}>
        <dt className="text-ink-muted">active version</dt>
        <dd>v{workflow.version}</dd>
        <dt className="text-ink-muted">open items</dt>
        <dd>{workflow.open_items}</dd>
        <dt className="text-ink-muted">routed here</dt>
        <dd>
          {routed.length > 0
            ? routed.map(condition).join("; ")
            : workflow.is_default
              ? "whatever no rule claims"
              : "only when a request names it"}
        </dd>
        {workflow.created_by && (
          <>
            <dt className="text-ink-muted">published</dt>
            <dd>
              {workflow.created_by}
              {workflow.updated_at ? `, ${shortDate(workflow.updated_at)}` : ""}
            </dd>
          </>
        )}
      </dl>
      {!workflow.is_default && (
        <div className="mt-4 flex flex-wrap items-start gap-2 border-t border-line pt-3">
          {routing && (
            <GesteConfirme
              ton="primary"
              question={`make ${nom} the default? a request that names no workflow and matches no rule is born in ${nom} from now on; tickets already running stay where they are.`}
              confirmer={`make ${nom} the default`}
              action={async () => {
                await api.putWorkflowRouting(slug, { ...routing, default: nom });
                await onChange();
              }}
            >
              make default
            </GesteConfirme>
          )}
          {pourquoiPas ? (
            <p className="self-center text-xs text-ink-muted">{pourquoiPas}</p>
          ) : (
            <GesteConfirme
              tonDuBouton="danger"
              question={`deactivate ${nom}? it takes no new request; its ${workflow.open_items} open ticket${workflow.open_items === 1 ? "" : "s"} finish on their version. publishing it again brings it back.`}
              confirmer={`deactivate ${nom}`}
              action={async () => {
                await api.deactivateWorkflow(slug, nom);
                await onChange();
              }}
            >
              deactivate
            </GesteConfirme>
          )}
        </div>
      )}
    </Card>
  );
}

/**
 * Les règles de routage, dans leur ordre : la première qui correspond l'emporte. On les change en
 * brouillon, et on enregistre d'un geste — une règle retirée par erreur ne part pas seule.
 */
function Routage({
  slug,
  routing,
  noms,
  onChange,
}: {
  slug: string;
  routing: WorkflowRouting;
  noms: string[];
  onChange: () => Promise<void>;
}) {
  const [regles, setRegles] = useState<Regle[] | null>(null);
  const courantes = regles ?? routing.rules;
  const [labels, setLabels] = useState("");
  const [type, setType] = useState("");
  const [cible, setCible] = useState("");
  const [occupe, setOccupe] = useState(false);
  const [erreur, setErreur] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const cibleChoisie = cible || noms.find((n) => n !== routing.default) || noms[0] || "";
  const nouvelle: Regle = {
    when: { labels_any: etiquettes(labels), labels_all: [], item_type: type.trim() || null },
    workflow: cibleChoisie,
  };
  const ajoutable = Boolean(cibleChoisie) && (nouvelle.when.labels_any!.length > 0 || Boolean(nouvelle.when.item_type));

  function changer(suivantes: Regle[]) {
    setMessage(null);
    setRegles(suivantes);
  }
  function deplacer(index: number, sens: -1 | 1) {
    const suivantes = [...courantes];
    const [regle] = suivantes.splice(index, 1);
    suivantes.splice(index + sens, 0, regle!);
    changer(suivantes);
  }

  async function enregistrer() {
    if (!regles) return;
    setOccupe(true);
    setErreur(null);
    try {
      await api.putWorkflowRouting(slug, { default: routing.default, rules: regles });
      setRegles(null);
      setMessage("routing saved — it applies to the next request that comes in");
      await onChange();
    } catch (cause) {
      setErreur(cause instanceof Error ? cause.message : "refused");
    } finally {
      setOccupe(false);
    }
  }

  return (
    <div className="space-y-4 text-sm">
      <p className="text-ink-muted">
        A request from a tracker goes to the first rule it matches, from the top; one that matches none goes to the
        default, <strong className="text-ink">{routing.default || "—"}</strong>.
      </p>
      {courantes.length === 0 ? (
        <p className="text-ink-muted">No rule — every request that names no workflow goes to the default.</p>
      ) : (
        <ol className="divide-y divide-line border-y border-line" aria-label="routing rules" data-testid="regles">
          {courantes.map((regle, index) => (
            <li key={`${index}-${regle.workflow}`} className="flex flex-wrap items-center gap-2 py-2">
              <span className="w-5 text-ink-muted">{index + 1}.</span>
              <span className="min-w-0 flex-1">
                {condition(regle)} → <strong>{regle.workflow}</strong>
              </span>
              <Button size="sm" onClick={() => deplacer(index, -1)} disabled={index === 0}>
                up<span className="sr-only"> rule {index + 1}</span>
              </Button>
              <Button size="sm" onClick={() => deplacer(index, 1)} disabled={index === courantes.length - 1}>
                down<span className="sr-only"> rule {index + 1}</span>
              </Button>
              <Button size="sm" onClick={() => changer(courantes.filter((_, i) => i !== index))}>
                remove<span className="sr-only"> rule {index + 1}</span>
              </Button>
            </li>
          ))}
        </ol>
      )}

      <form
        className="flex flex-wrap items-end gap-2"
        aria-label="new routing rule"
        onSubmit={(event) => {
          event.preventDefault();
          if (!ajoutable) return;
          changer([...courantes, nouvelle]);
          setLabels("");
          setType("");
        }}
      >
        <label className="flex min-w-0 flex-col gap-1">
          <span className="text-xs text-ink-muted">labelled (any of)</span>
          <input
            value={labels}
            onChange={(event) => setLabels(event.target.value)}
            placeholder="incident, sev1"
            className="rounded border border-line bg-surface px-2 py-1"
          />
        </label>
        <label className="flex min-w-0 flex-col gap-1">
          <span className="text-xs text-ink-muted">of type</span>
          <input
            value={type}
            onChange={(event) => setType(event.target.value)}
            placeholder="Bug"
            className="rounded border border-line bg-surface px-2 py-1"
          />
        </label>
        <label className="flex min-w-0 flex-col gap-1">
          <span className="text-xs text-ink-muted">goes to</span>
          <select
            value={cibleChoisie}
            onChange={(event) => setCible(event.target.value)}
            className="rounded border border-line bg-surface px-2 py-1"
          >
            {noms.map((nom) => (
              <option key={nom} value={nom}>
                {nom}
              </option>
            ))}
          </select>
        </label>
        <Button size="sm" type="submit" disabled={!ajoutable} aria-describedby="regle-incomplete">
          add the rule
        </Button>
        {!ajoutable && (
          <span id="regle-incomplete" className="self-center text-xs text-ink-muted">
            a rule needs a label or a type
          </span>
        )}
      </form>

      {regles && (
        <div className="flex flex-wrap items-center gap-2 border border-line border-l-2 border-l-accent p-2">
          <span className="flex-1">the routing has unsaved changes</span>
          <Button size="sm" onClick={() => setRegles(null)} disabled={occupe}>
            discard
          </Button>
          <Button size="sm" tone="primary" onClick={() => void enregistrer()} disabled={occupe}>
            save the routing
          </Button>
        </div>
      )}
      {erreur && (
        <ErrorNote>
          <PhraseDuMoteur texte={erreur} />
        </ErrorNote>
      )}
      {message && <p role="status">{message}</p>}
    </div>
  );
}
