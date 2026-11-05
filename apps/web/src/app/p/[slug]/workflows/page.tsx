// SPDX-License-Identifier: Apache-2.0
"use client";

import Link from "next/link";
import { DEFAULT_WORKFLOW_HINT } from "@/components/workflows/timing";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { use, useState } from "react";
import { ConfirmedGesture } from "@/components/confirmed-gesture";
import { RoutingCalibrationCard } from "@/components/workflows/routing-calibration";
import { cn } from "@/lib/cn";
import { workflowHueClasses } from "@/lib/workflow-hue";
import { Workflow as WorkflowIcon } from "lucide-react";
import { Badge, Button, buttonClasses, Card, Empty, ErrorNote, ReadState, Input, EngineSentence, Select, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { shortDate } from "@/lib/format";
import type { RoutingPreview, WorkflowRouting, WorkflowSummary } from "@/lib/types";

type Rule = WorkflowRouting["rules"][number];
type Strategy = NonNullable<WorkflowRouting["strategy"]>;

/** The three strategies of ADR 0116, each in one sentence. */
const STRATEGIES: Array<{ value: Strategy; label: string; says: string }> = [
  { value: "labels", label: "Labels and types", says: "The rules alone; a request that matches none goes to the default." },
  {
    value: "rules_then_llm",
    label: "Rules, then a small model",
    says: "The rules first; when none matches, a small model reads the request and picks a workflow, else the default.",
  },
  {
    value: "llm",
    label: "A small model",
    says: "A small model reads every request and picks a workflow, else the default; the rules are kept but not read.",
  },
];

/** A routing rule's conditions, in plain words: "labelled incident", "type Bug". */
function condition(rule: Rule): string {
  const parts: string[] = [];
  if (rule.when.labels_any?.length) parts.push(`labelled ${rule.when.labels_any.join(" or ")}`);
  if (rule.when.labels_all?.length) parts.push(`labelled ${rule.when.labels_all.join(" and ")}`);
  if (rule.when.item_type) parts.push(`of type ${rule.when.item_type}`);
  return parts.join(", ") || "always";
}

/** "incident, sev1" -> ["incident", "sev1"]: labels, with no blank and no duplicate. */
function labelsOf(text: string): string[] {
  return [
    ...new Set(
      text
        .split(",")
        .map((e) => e.trim())
        .filter(Boolean),
    ),
  ];
}

/**
 * The project's workflows (ADR 0031): a project carries as many as it has processes: arrival and
 * departure for an HR project, delivery and hotfix for a product team. Each card gives its
 * active version, whether it is the default, what is routed to it and how many tickets are pinned to it.
 *
 * The API already knew how to choose the default, route by label or by type, and deactivate a workflow;
 * the console could only say so (second pass of 8 Oct, S23-13). These three gestures are here.
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
          <Link href={`/p/${slug}/workflows/library`} className={buttonClasses("secondary", "sm")}>
            Browse the library
          </Link>
          <Link href={`/p/${slug}/workflows/new`} className={buttonClasses("secondary", "sm")}>
            New workflow
          </Link>
        </div>
      </div>

      {workflows.data?.length === 0 && <Empty title="No workflow" icon={WorkflowIcon} hue={2}>Publish one from a template.</Empty>}
      <ul className="grid gap-4 md:grid-cols-2" aria-label="Workflows">
        {workflows.data?.map((workflow) => (
          <li key={workflow.name}>
            <WorkflowCard
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
            <ReadState lecture={routing} quoi="the routing" />
            <ReadState lecture={workflows} quoi="the workflows" />
          </>
        ) : (
          <Routing
            slug={slug}
            routing={routing.data}
            workflows={workflows.data}
            onChange={relire}
          />
        )}
      </Card>
      {/* How good the saved routing's model was, once a strategy asks one (ADR 0123 §4, S52-12). */}
      {routing.data && (routing.data.strategy ?? "labels") !== "labels" && (
        <Card title="How good the routing is">
          <RoutingCalibrationCard slug={slug} />
        </Card>
      )}
      {routing.data && workflows.data && workflows.data.length > 0 && (
        <Card title="Try the routing">
          <TryRouting slug={slug} />
        </Card>
      )}
    </div>
  );
}

function WorkflowCard({
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
  routed: Rule[];
  onChange: () => Promise<void>;
}) {
  const name = workflow.name;
  // What the API would refuse, said beforehand: the default and a rule's target cannot be deactivated.
  const whyNot = workflow.is_default
    ? "The default cannot be deactivated — make another one the default first"
    : routed.length > 0
      ? "A routing rule sends requests here — remove it first"
      : null;
  return (
    <Card
      // Its own colour on its edge and its dot (ADR 0120): the same as on the board and on its tickets.
      className={cn("border-t-4", workflowHueClasses(name).stripe)}
      title={
        <span className="inline-flex items-center gap-2">
          <span aria-hidden className={cn("size-2.5 shrink-0 rounded-full", workflowHueClasses(name).solid)} />
          <Link href={`/p/${slug}/workflows/${encodeURIComponent(name)}`} className="hover:underline">
            {name}
          </Link>
        </span>
      }
      action={workflow.is_default ? <Badge tone="neutral" title={DEFAULT_WORKFLOW_HINT}>Default</Badge> : undefined}
    >
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm" data-testid={`workflow-card-${name}`}>
        <dt className="text-ink-muted">Active version</dt>
        <dd>v{workflow.version}</dd>
        <dt className="text-ink-muted">Open items</dt>
        <dd>{workflow.open_items}</dd>
        <dt className="text-ink-muted">Routed here</dt>
        <dd>
          {[
            ...routed.map(condition),
            ...((routing?.strategy ?? "labels") !== "labels" ? ["when the model chooses it"] : []),
          ].join("; ") || (workflow.is_default ? "Whatever no rule claims" : "Only when a request names it")}
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
            <ConfirmedGesture
              ton="primary"
              question={`Make ${name} the default? A request that names no workflow and matches no rule is born in ${name} from now on; tickets already running stay where they are.`}
              confirmer={`Make ${name} the default`}
              action={async () => {
                await api.putWorkflowRouting(slug, { ...routing, default: name });
                await onChange();
              }}
            >
              Make default
            </ConfirmedGesture>
          )}
          {whyNot ? (
            <p className="self-center text-xs text-ink-muted">{whyNot}</p>
          ) : (
            <ConfirmedGesture
              tonDuBouton="danger"
              question={`Deactivate ${name}? It takes no new request; its ${workflow.open_items} open ticket${workflow.open_items === 1 ? "" : "s"} finish on their version. Publishing it again brings it back.`}
              confirmer={`Deactivate ${name}`}
              action={async () => {
                await api.deactivateWorkflow(slug, name);
                await onChange();
              }}
            >
              Deactivate
            </ConfirmedGesture>
          )}
        </div>
      )}
      {workflow.can_share && org && (
        // Shown only when the server says so (ADR 0074); sharing copies this version (ADR 0111 §4).
        <div className="mt-3 border-t border-line pt-3">
          <ConfirmedGesture
            question={`Share ${name} v${workflow.version} to the organisation's catalogue? Every project of ${org} can then adopt a copy; later changes here reach the catalogue only when shared again.`}
            confirmer={`Share ${name}`}
            action={async () => {
              await api.shareWorkflow(org, { project: slug, name: name });
              await onChange();
            }}
          >
            Share to the catalogue
          </ConfirmedGesture>
        </div>
      )}
    </Card>
  );
}

/**
 * The routing rules, in their order: the first that matches wins. They are changed in a
 * draft, and saved in one gesture: a rule removed by mistake does not leave alone.
 *
 * A rule is edited in place: "Edit" loads it into the form, which then updates it instead of adding
 * one. The default is part of the same draft, so a project with none can choose it here.
 */
function Routing({
  slug,
  routing,
  workflows,
  onChange,
}: {
  slug: string;
  routing: WorkflowRouting;
  workflows: WorkflowSummary[];
  onChange: () => Promise<void>;
}) {
  const names = workflows.map((w) => w.name);
  const [regles, setRegles] = useState<Rule[] | null>(null);
  const courantes = regles ?? routing.rules;
  const [defaultDraft, setDefaultDraft] = useState<string | null>(null);
  const currentDefault = defaultDraft ?? routing.default;
  const [strategyDraft, setStrategyDraft] = useState<Strategy | null>(null);
  const currentStrategy: Strategy = strategyDraft ?? routing.strategy ?? "labels";
  const [hintsDraft, setHintsDraft] = useState<Record<string, string> | null>(null);
  const currentHints = hintsDraft ?? routing.hints ?? {};
  // The minimum confidence as typed: "" keeps every answer (ADR 0121).
  const [confidenceDraft, setConfidenceDraft] = useState<string | null>(null);
  const savedConfidence = routing.min_confidence == null ? "" : String(routing.min_confidence);
  const currentConfidence = confidenceDraft ?? savedConfidence;
  const parsedConfidence = currentConfidence.trim() === "" ? null : Number(currentConfidence);
  const confidenceValid =
    parsedConfidence === null || (Number.isFinite(parsedConfidence) && parsedConfidence >= 0 && parsedConfidence <= 1);
  const [labels, setLabels] = useState("");
  const [labelsAll, setLabelsAll] = useState("");
  const [type, setType] = useState("");
  const [cible, setCible] = useState("");
  const [editing, setEditing] = useState<number | null>(null);
  const [occupe, setOccupe] = useState(false);
  const [erreur, setErreur] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const chosenTarget = cible || names.find((n) => n !== currentDefault) || names[0] || "";
  const newRule: Rule = {
    when: { labels_any: labelsOf(labels), labels_all: labelsOf(labelsAll), item_type: type.trim() || null },
    workflow: chosenTarget,
  };
  const ajoutable =
    Boolean(chosenTarget) &&
    (newRule.when.labels_any!.length > 0 || newRule.when.labels_all!.length > 0 || Boolean(newRule.when.item_type));
  const dirty =
    regles !== null || defaultDraft !== null || strategyDraft !== null || hintsDraft !== null || confidenceDraft !== null;

  function changer(suivantes: Rule[]) {
    setMessage(null);
    setRegles(suivantes);
  }
  function deplacer(index: number, sens: -1 | 1) {
    const suivantes = [...courantes];
    const [rule] = suivantes.splice(index, 1);
    suivantes.splice(index + sens, 0, rule!);
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
    setStrategyDraft(null);
    setHintsDraft(null);
    setConfidenceDraft(null);
    resetForm();
  }

  async function enregistrer() {
    if (!dirty || !currentDefault || !confidenceValid) return;
    setOccupe(true);
    setErreur(null);
    try {
      await api.putWorkflowRouting(slug, {
        default: currentDefault,
        rules: courantes,
        strategy: currentStrategy,
        hints: currentHints,
        // Absent: unchanged. Sent only once it was changed here.
        ...(confidenceDraft !== null ? { min_confidence: parsedConfidence } : {}),
      });
      setRegles(null);
      setDefaultDraft(null);
      setStrategyDraft(null);
      setHintsDraft(null);
      setConfidenceDraft(null);
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
      <label className="flex flex-wrap items-center gap-2">
        <span className="text-ink-muted">Strategy</span>
        <Select
          value={currentStrategy}
          onChange={(event) => {
            setMessage(null);
            const next = event.target.value as Strategy;
            setStrategyDraft(next === (routing.strategy ?? "labels") ? null : next);
          }}
          className="w-auto"
        >
          {STRATEGIES.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </Select>
      </label>
      <p className="text-ink-muted" data-testid="strategy-says">
        {STRATEGIES.find((s) => s.value === currentStrategy)!.says} A request that names its workflow always goes there.
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
          {names.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </Select>
      </label>
      {courantes.length === 0 ? (
        <p className="text-ink-muted">No rule — every request that names no workflow goes to the default.</p>
      ) : (
        <ol className="divide-y divide-line border-y border-line" aria-label="Routing rules" data-testid="rules">
          {courantes.map((rule, index) => (
            <li
              key={`${index}-${rule.workflow}`}
              className={`flex flex-wrap items-center gap-2 py-2 ${editing === index ? "bg-surface-muted" : ""}`}
              aria-current={editing === index ? "true" : undefined}
            >
              <span className="w-5 text-ink-muted">{index + 1}.</span>
              <span className="min-w-0 flex-1">
                {condition(rule)} → <strong>{rule.workflow}</strong>
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
            editing === null ? [...courantes, newRule] : courantes.map((r, i) => (i === editing ? newRule : r)),
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
            value={chosenTarget}
            onChange={(event) => setCible(event.target.value)}
            className="w-auto"
          >
            {names.map((name) => (
              <option key={name} value={name}>
                {name}
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

      {currentStrategy !== "labels" && (
        <fieldset className="space-y-3 border-t border-line pt-3" aria-label="What the model reads">
          <legend className="text-xs text-ink-muted">
            What the model reads about each workflow: its description, and a hint you write here — the requests it
            takes, in the words people use.
          </legend>
          {workflows.map((w) => (
            <label key={w.name} className="block space-y-1">
              <span className="font-medium">{w.name}</span>
              <span className="block text-xs text-ink-muted">{w.description || "No description in its YAML."}</span>
              <Textarea
                rows={2}
                maxLength={500}
                aria-label={`Routing hint for ${w.name}`}
                value={currentHints[w.name] ?? ""}
                placeholder="new hires, arrivals, first day"
                onChange={(event) => {
                  setMessage(null);
                  setHintsDraft({ ...currentHints, [w.name]: event.target.value });
                }}
                className="w-full"
              />
            </label>
          ))}
          <label className="block space-y-1">
            <span className="font-medium">Minimum confidence</span>
            <span id="min-confidence-says" className="block text-xs text-ink-muted">
              A decision model (a catalogue entry enabled for route and decide) answers with a probability; below this
              one, the default takes the request. Empty: every answer is taken. A chat model gives no probability and is
              never held to it.
            </span>
            <Input
              type="number"
              inputMode="decimal"
              min={0}
              max={1}
              step={0.05}
              aria-label="Minimum confidence"
              aria-describedby="min-confidence-says"
              aria-invalid={!confidenceValid}
              value={currentConfidence}
              placeholder="0.5"
              onChange={(event) => {
                setMessage(null);
                setConfidenceDraft(event.target.value === savedConfidence ? null : event.target.value);
              }}
              className="w-32"
            />
            {!confidenceValid && <span className="block text-xs text-danger">A probability between 0 and 1</span>}
          </label>
        </fieldset>
      )}

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
            disabled={occupe || !currentDefault || editing !== null || !confidenceValid}
          >
            Save the routing
          </Button>
        </div>
      )}
      {erreur && (
        <ErrorNote>
          <EngineSentence text={erreur} />
        </ErrorNote>
      )}
      {message && <p role="status">{message}</p>}
    </div>
  );
}

/** Where a request would be born, with the saved routing (ADR 0116): nothing is created. */
function TryRouting({ slug }: { slug: string }) {
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [labels, setLabels] = useState("");
  const [occupe, setOccupe] = useState(false);
  const [erreur, setErreur] = useState<string | null>(null);
  const [answer, setAnswer] = useState<RoutingPreview | null>(null);

  async function essayer() {
    setOccupe(true);
    setErreur(null);
    try {
      setAnswer(
        await api.previewWorkflowRouting(slug, { title: title.trim(), body: body.trim() || null, labels: labelsOf(labels) }),
      );
    } catch (cause) {
      setErreur(cause instanceof Error ? cause.message : "refused");
    } finally {
      setOccupe(false);
    }
  }

  const BY: Record<RoutingPreview["by"], string> = {
    explicit: "named in the request",
    rule: "by a rule",
    llm: "chosen by the model",
    default: "the default",
  };
  return (
    <form
      className="space-y-2 text-sm"
      aria-label="Try the routing"
      onSubmit={(event) => {
        event.preventDefault();
        if (title.trim()) void essayer();
      }}
    >
      <p className="text-ink-muted">
        A request as it would arrive, against the saved routing. Nothing is created; when the model is asked, its call is
        booked in the costs.
      </p>
      <label className="block space-y-1">
        <span className="text-xs text-ink-muted">Title</span>
        <Input value={title} onChange={(event) => setTitle(event.target.value)} className="w-full" />
      </label>
      <label className="block space-y-1">
        <span className="text-xs text-ink-muted">Description</span>
        <Textarea rows={3} value={body} onChange={(event) => setBody(event.target.value)} className="w-full" />
      </label>
      <label className="block space-y-1">
        <span className="text-xs text-ink-muted">Labels</span>
        <Input value={labels} onChange={(event) => setLabels(event.target.value)} placeholder="incident, sev1" className="w-full" />
      </label>
      <Button size="sm" type="submit" disabled={occupe || !title.trim()}>
        Try
      </Button>
      {answer && (
        <p role="status" data-testid="routing-preview">
          → <strong>{answer.workflow ?? "no workflow"}</strong> ({BY[answer.by]})
          {answer.reason ? `: ${answer.reason}` : ""}
          {answer.model ? ` — ${answer.model}` : ""}
          {answer.confidence != null ? `, confidence ${answer.confidence.toFixed(2)}` : ""}
        </p>
      )}
      {erreur && (
        <ErrorNote>
          <EngineSentence text={erreur} />
        </ErrorNote>
      )}
    </form>
  );
}
