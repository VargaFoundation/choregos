// SPDX-License-Identifier: Apache-2.0
"use client";

import Link from "next/link";
import { DEFAULT_WORKFLOW_HINT } from "@/components/workflows/timing";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { use, useState } from "react";
import { GesteConfirme } from "@/components/geste-confirme";
import { Badge, Button, buttonClasses, Card, Empty, ErrorNote, EtatDeLecture, Input, PhraseDuMoteur, Select } from "@/components/ui";
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
  const project = useQuery({ queryKey: ["project", slug], queryFn: () => api.project(slug) });

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
        <div className="flex flex-wrap gap-2">
          <Link href={`/p/${slug}/workflows/catalogue`} className={buttonClasses("secondary", "sm")}>
            Browse the catalogue
          </Link>
          <Link href={`/p/${slug}/workflows/new`} className={buttonClasses("secondary", "sm")}>
            New workflow
          </Link>
        </div>
      </div>

      {workflows.data?.length === 0 && <Empty title="No workflow">Publish one from a template.</Empty>}
      <ul className="grid gap-4 md:grid-cols-2" aria-label="Workflows">
        {workflows.data?.map((workflow) => (
          <li key={workflow.name}>
            <CarteDeWorkflow
              slug={slug}
              org={project.data?.org ?? ""}
              workflow={workflow}
              routing={routing.data}
              routed={rules.filter((rule) => rule.workflow === workflow.name)}
              onChange={relire}
            />
          </li>
        ))}
      </ul>

      <Card title="Routing">
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
  org,
  workflow,
  routing,
  routed,
  onChange,
}: {
  slug: string;
  org: string;
  workflow: WorkflowSummary;
  routing: WorkflowRouting | undefined;
  routed: Regle[];
  onChange: () => Promise<void>;
}) {
  const nom = workflow.name;
  // Ce que l'API refuserait, dit avant : le défaut et la cible d'une règle ne se désactivent pas.
  const pourquoiPas = workflow.is_default
    ? "The default cannot be deactivated — make another one the default first"
    : routed.length > 0
      ? "A routing rule sends requests here — remove it first"
      : null;
  return (
    <Card
      title={
        <Link href={`/p/${slug}/workflows/${encodeURIComponent(nom)}`} className="hover:underline">
          {nom}
        </Link>
      }
      action={workflow.is_default ? <Badge tone="neutral" title={DEFAULT_WORKFLOW_HINT}>Default</Badge> : undefined}
    >
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm" data-testid={`workflow-card-${nom}`}>
        <dt className="text-ink-muted">Active version</dt>
        <dd>v{workflow.version}</dd>
        <dt className="text-ink-muted">Open items</dt>
        <dd>{workflow.open_items}</dd>
        <dt className="text-ink-muted">Routed here</dt>
        <dd>
          {routed.length > 0
            ? routed.map(condition).join("; ")
            : workflow.is_default
              ? "Whatever no rule claims"
              : "Only when a request names it"}
        </dd>
        {workflow.created_by && (
          <>
            <dt className="text-ink-muted">Published</dt>
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
              question={`Make ${nom} the default? A request that names no workflow and matches no rule is born in ${nom} from now on; tickets already running stay where they are.`}
              confirmer={`Make ${nom} the default`}
              action={async () => {
                await api.putWorkflowRouting(slug, { ...routing, default: nom });
                await onChange();
              }}
            >
              Make default
            </GesteConfirme>
          )}
          {pourquoiPas ? (
            <p className="self-center text-xs text-ink-muted">{pourquoiPas}</p>
          ) : (
            <GesteConfirme
              tonDuBouton="danger"
              question={`Deactivate ${nom}? It takes no new request; its ${workflow.open_items} open ticket${workflow.open_items === 1 ? "" : "s"} finish on their version. Publishing it again brings it back.`}
              confirmer={`Deactivate ${nom}`}
              action={async () => {
                await api.deactivateWorkflow(slug, nom);
                await onChange();
              }}
            >
              Deactivate
            </GesteConfirme>
          )}
        </div>
      )}
      {workflow.can_share && org && (
        // Shown only when the server says so (ADR 0074); sharing copies this version (ADR 0111 §4).
        <div className="mt-3 border-t border-line pt-3">
          <GesteConfirme
            question={`Share ${nom} v${workflow.version} to the organisation's catalogue? Every project of ${org} can then adopt a copy; later changes here reach the catalogue only when shared again.`}
            confirmer={`Share ${nom}`}
            action={async () => {
              await api.shareWorkflow(org, { project: slug, name: nom });
              await onChange();
            }}
          >
            Share to the catalogue
          </GesteConfirme>
        </div>
      )}
    </Card>
  );
}

/**
 * Les règles de routage, dans leur ordre : la première qui correspond l'emporte. On les change en
 * brouillon, et on enregistre d'un geste — une règle retirée par erreur ne part pas seule.
 *
 * A rule is edited in place: "Edit" loads it into the form, which then updates it instead of adding
 * one. The default is part of the same draft, so a project with none can choose it here.
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
  const [defaultDraft, setDefaultDraft] = useState<string | null>(null);
  const currentDefault = defaultDraft ?? routing.default;
  const [labels, setLabels] = useState("");
  const [labelsAll, setLabelsAll] = useState("");
  const [type, setType] = useState("");
  const [cible, setCible] = useState("");
  const [editing, setEditing] = useState<number | null>(null);
  const [occupe, setOccupe] = useState(false);
  const [erreur, setErreur] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const cibleChoisie = cible || noms.find((n) => n !== currentDefault) || noms[0] || "";
  const nouvelle: Regle = {
    when: { labels_any: etiquettes(labels), labels_all: etiquettes(labelsAll), item_type: type.trim() || null },
    workflow: cibleChoisie,
  };
  const ajoutable =
    Boolean(cibleChoisie) &&
    (nouvelle.when.labels_any!.length > 0 || nouvelle.when.labels_all!.length > 0 || Boolean(nouvelle.when.item_type));
  const dirty = regles !== null || defaultDraft !== null;

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
  function resetForm() {
    setEditing(null);
    setLabels("");
    setLabelsAll("");
    setType("");
    setCible("");
  }
  function edit(index: number) {
    const rule = courantes[index]!;
    setEditing(index);
    setLabels((rule.when.labels_any ?? []).join(", "));
    setLabelsAll((rule.when.labels_all ?? []).join(", "));
    setType(rule.when.item_type ?? "");
    setCible(rule.workflow);
  }
  function discard() {
    setRegles(null);
    setDefaultDraft(null);
    resetForm();
  }

  async function enregistrer() {
    if (!dirty || !currentDefault) return;
    setOccupe(true);
    setErreur(null);
    try {
      await api.putWorkflowRouting(slug, { default: currentDefault, rules: courantes });
      setRegles(null);
      setDefaultDraft(null);
      setMessage("Routing saved — it applies to the next request that comes in");
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
        default.
      </p>
      <label className="flex flex-wrap items-center gap-2">
        <span className="text-ink-muted">Default workflow</span>
        <Select
          value={currentDefault}
          onChange={(event) => {
            setMessage(null);
            setDefaultDraft(event.target.value === routing.default ? null : event.target.value);
          }}
          className="w-auto"
        >
          {!currentDefault && (
            <option value="" disabled>
              Choose the default
            </option>
          )}
          {noms.map((nom) => (
            <option key={nom} value={nom}>
              {nom}
            </option>
          ))}
        </Select>
      </label>
      {courantes.length === 0 ? (
        <p className="text-ink-muted">No rule — every request that names no workflow goes to the default.</p>
      ) : (
        <ol className="divide-y divide-line border-y border-line" aria-label="Routing rules" data-testid="regles">
          {courantes.map((regle, index) => (
            <li
              key={`${index}-${regle.workflow}`}
              className={`flex flex-wrap items-center gap-2 py-2 ${editing === index ? "bg-surface-muted" : ""}`}
              aria-current={editing === index ? "true" : undefined}
            >
              <span className="w-5 text-ink-muted">{index + 1}.</span>
              <span className="min-w-0 flex-1">
                {condition(regle)} → <strong>{regle.workflow}</strong>
              </span>
              <Button
                size="sm"
                aria-label={`Edit rule ${index + 1}`}
                onClick={() => edit(index)}
                disabled={editing !== null}
              >
                Edit
              </Button>
              <Button
                size="sm"
                aria-label={`Up rule ${index + 1}`}
                onClick={() => deplacer(index, -1)}
                disabled={index === 0 || editing !== null}
              >
                Up
              </Button>
              <Button
                size="sm"
                aria-label={`Down rule ${index + 1}`}
                onClick={() => deplacer(index, 1)}
                disabled={index === courantes.length - 1 || editing !== null}
              >
                Down
              </Button>
              <Button
                size="sm"
                aria-label={`Remove rule ${index + 1}`}
                onClick={() => changer(courantes.filter((_, i) => i !== index))}
                disabled={editing !== null}
              >
                Remove
              </Button>
            </li>
          ))}
        </ol>
      )}

      <form
        className="flex flex-wrap items-end gap-2"
        aria-label={editing === null ? "New routing rule" : `Edit routing rule ${editing + 1}`}
        onSubmit={(event) => {
          event.preventDefault();
          if (!ajoutable) return;
          changer(
            editing === null ? [...courantes, nouvelle] : courantes.map((r, i) => (i === editing ? nouvelle : r)),
          );
          resetForm();
        }}
      >
        <label className="flex min-w-0 flex-col gap-1">
          <span className="text-xs text-ink-muted">Labelled (any of)</span>
          <Input
            value={labels}
            onChange={(event) => setLabels(event.target.value)}
            placeholder="incident, sev1"
            className="w-auto"
          />
        </label>
        <label className="flex min-w-0 flex-col gap-1">
          <span className="text-xs text-ink-muted">Labelled (all of)</span>
          <Input
            value={labelsAll}
            onChange={(event) => setLabelsAll(event.target.value)}
            placeholder="customer, urgent"
            className="w-auto"
          />
        </label>
        <label className="flex min-w-0 flex-col gap-1">
          <span className="text-xs text-ink-muted">Of type</span>
          <Input
            value={type}
            onChange={(event) => setType(event.target.value)}
            placeholder="Bug"
            className="w-auto"
          />
        </label>
        <label className="flex min-w-0 flex-col gap-1">
          <span className="text-xs text-ink-muted">Goes to</span>
          <Select
            value={cibleChoisie}
            onChange={(event) => setCible(event.target.value)}
            className="w-auto"
          >
            {noms.map((nom) => (
              <option key={nom} value={nom}>
                {nom}
              </option>
            ))}
          </Select>
        </label>
        <Button size="sm" type="submit" disabled={!ajoutable} aria-describedby="regle-incomplete">
          {editing === null ? "Add the rule" : `Update rule ${editing + 1}`}
        </Button>
        {editing !== null && (
          <Button size="sm" onClick={resetForm}>
            Cancel
          </Button>
        )}
        {!ajoutable && (
          <span id="regle-incomplete" className="self-center text-xs text-ink-muted">
            A rule needs a label or a type
          </span>
        )}
      </form>

      {dirty && (
        <div className="flex flex-wrap items-center gap-2 border border-line border-l-2 border-l-accent p-2">
          <span className="flex-1">
            {currentDefault ? "The routing has unsaved changes" : "Choose a default workflow before saving"}
          </span>
          <Button size="sm" onClick={discard} disabled={occupe}>
            Discard
          </Button>
          <Button
            size="sm"
            tone="primary"
            onClick={() => void enregistrer()}
            disabled={occupe || !currentDefault || editing !== null}
          >
            Save the routing
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
