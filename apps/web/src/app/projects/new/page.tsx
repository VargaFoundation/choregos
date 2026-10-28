// SPDX-License-Identifier: Apache-2.0
"use client";

import { cn } from "@/lib/cn";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button, Card, ErrorNote, Heading, Input, Select, StepNumber } from "@/components/ui";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";

const STEPS = ["template", "project", "connectors", "summary"];
const TRACKERS = [
  { value: "internal", label: "Choregos (requests are filed here)" },
  { value: "github", label: "GitHub Issues / Projects" },
  { value: "jira", label: "Jira" },
  { value: "gitlab", label: "GitLab" },
];

/**
 * Création d'un projet : template → projet → connecteurs → récapitulatif → provisioning.
 *
 * Le dépôt est FACULTATIF (ADR 0012) : un métier sans code n'en a pas, et l'assistant
 * l'exigeait quand même. Les templates viennent de l'API, pas d'une liste codée en dur ;
 * le tracker se choisit ici, et « Choregos » signifie que les demandes se posent dans
 * l'outil (`items create`, ou le board).
 */
export default function NewProjectPage() {
  const router = useRouter();
  const { org } = useSession();
  const templates = useQuery({ queryKey: ["templates"], queryFn: () => api.templates() });
  // Without a template, the project may start from a catalogue workflow (ADR 0108 §7).
  const catalogue = useQuery({ queryKey: ["workflow-catalogue", org], queryFn: () => api.workflowCatalogue(org) });
  const [step, setStep] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Ce qui a déjà été créé : un échec au milieu ne se rejoue pas depuis le début (S23-06). Avant, le
  // projet était créé, le tracker refusé, et « create » renvoyait ensuite 409 pour toujours.
  const [fait, setFait] = useState<{ projet: { id: string; slug: string } | null; tracker: boolean; workflow?: boolean }>({
    projet: null,
    tracker: false,
  });
  const [form, setForm] = useState({
    template: "",
    workflow: "",
    slug: "",
    name: "",
    sansDepot: false,
    repo: "",
    language: "python",
    tracker: "internal",
    gitops: "",
    channel: "#choregos",
  });

  function set<K extends keyof typeof form>(key: K, value: (typeof form)[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  const slugValid = /^[a-z0-9][a-z0-9-]{0,62}$/.test(form.slug);
  const repoValid = form.sansDepot || form.repo.startsWith("http") || /^[\w.-]+\/[\w.-]+$/.test(form.repo);
  const avecTemplate = !form.sansDepot && form.template !== "";
  // The workflow the platform gives a project without a template (ADR 0107): adopting it again is moot.
  const automatique = form.sansDepot ? "core/default-request" : "core/default-simple";
  const offerts = (catalogue.data ?? []).filter((e) => !form.sansDepot || !e.needs_repository);
  const choisi = avecTemplate ? undefined : offerts.find((e) => `${e.source}/${e.name}` === form.workflow);

  async function create() {
    setBusy(true);
    setError(null);
    let projet = fait.projet;
    try {
      const config: Record<string, unknown> = { slug: form.slug, org };
      if (!form.sansDepot) {
        config.repo = {
          url: form.repo.startsWith("http") ? form.repo : `https://github.com/${form.repo}.git`,
          default_branch: "main",
          language: form.language,
        };
        if (form.gitops) config.gitops = { repo_url: form.gitops, apps: [form.slug] };
      }
      if (form.channel) config.notify = { slack_channel: form.channel };
      if (!projet) {
        const cree = await api.createProject(org, {
          slug: form.slug,
          name: form.name || form.slug,
          template_ref: avecTemplate ? form.template : null,
          config,
        });
        projet = { id: cree.id, slug: cree.slug };
        setFait({ projet, tracker: false });
      }
      if (!fait.tracker) {
        await api.putConnector(projet.id, "tracker", { type: form.tracker, config: {} });
        setFait({ projet, tracker: true });
      }
      if (choisi && form.workflow !== automatique && !fait.workflow) {
        await api.adoptWorkflow(projet.id, { source: choisi.source, name: choisi.name, version: choisi.version, make_default: true });
        setFait({ projet, tracker: true, workflow: true });
      }
      // Without a template, a project with no repository has nothing to provision: the API activates it.
      if (avecTemplate || form.sansDepot) await api.provision(projet.id);
      router.push(`/p/${projet.slug}`);
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "refused";
      setError(
        projet
          ? `${projet.slug} was created, but ${fait.tracker ? "provisioning" : "connecting its tracker"} failed: ${message}`
          : `creation refused: ${message}`,
      );
      setBusy(false);
    }
  }

  const raisonDAttente =
    step === 1 && !form.slug
      ? "an identifier is needed to go on"
      : step === 1 && !slugValid
        ? "the identifier is not valid yet"
        : step === 1 && !repoValid
          ? form.repo
            ? "the repository is not valid yet"
            : "a repository is needed — or tick “no code repository” in the first step"
          : null;

  const recapitulatif: [string, string][] = [
    ["Organisation", org],
    ["Template", form.sansDepot ? "None — no code repository" : form.template || "None"],
    ...(avecTemplate ? [] : ([["Workflow", choisi ? `${choisi.title} (${choisi.source})` : "Automatic"]] as [string, string][])),
    ["Identifier", form.slug],
    ["Display name", form.name || form.slug],
    ["Repository", form.sansDepot ? "None" : form.repo],
    ...(form.sansDepot ? [] : ([["Main language", form.language]] as [string, string][])),
    ["Tracker", TRACKERS.find((t) => t.value === form.tracker)?.label ?? form.tracker],
    ...(form.sansDepot ? [] : ([["GitOps repository", form.gitops]] as [string, string][])),
    ["Slack channel", form.channel],
  ];

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <Heading as="h1" size="xl">
        New project
      </Heading>
      {/* Deux colonnes sur téléphone, quatre au-delà : à 360 px, quatre cases de 78 px ne tenaient
          pas un numéro et « connectors », et la page débordait (S23-01). */}
      <ol className="grid grid-cols-2 border border-line sm:grid-cols-4">
        {STEPS.map((label, index) => (
          <li
            key={label}
            aria-current={index === step ? "step" : undefined}
            className={cn(
              "flex min-w-0 items-center gap-3 p-3 text-xs",
              index % 2 === 1 && "border-l border-line",
              index >= 2 && "border-t border-line sm:border-t-0",
              index === 2 && "sm:border-l",
              index === step ? "text-ink" : "text-ink-muted",
            )}
          >
            <StepNumber
              value={index + 1}
              className={cn(index <= step && "border-accent bg-accent text-accent-ink")}
            />
            <span className={cn(index === step && "font-bold")}>{label}</span>
          </li>
        ))}
      </ol>

      <Card>
        {step === 0 && (
          <div className="space-y-3 text-sm">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={form.sansDepot}
                onChange={(event) => set("sansDepot", event.target.checked)}
              />
              <span>This project has no code repository (a line of business: HR, purchasing, legal…)</span>
            </label>
            {!form.sansDepot && (
              <label className="block space-y-1">
                <span>Stack template</span>
                <Select
                  value={form.template}
                  onChange={(event) => set("template", event.target.value)}
                  className="w-full"
                >
                  <option value="">None — I wire my connectors myself</option>
                  {(templates.data ?? []).map((template) => (
                    <option key={`${template.name}@${template.version}`} value={`${template.name}@${template.version}`}>
                      {template.display}
                      {template.is_published ? "" : " (unpublished)"}
                    </option>
                  ))}
                </Select>
                <span className="block text-xs text-ink-muted">
                  A template installs the labels, the board, the webhooks, the namespaces and the CI.
                </span>
              </label>
            )}
            {!avecTemplate && (
              <label className="block space-y-1">
                <span>Workflow</span>
                <Select aria-label="Workflow" value={form.workflow} onChange={(event) => set("workflow", event.target.value)} className="w-full">
                  <option value="">
                    Automatic — {form.sansDepot ? "default-request (needs no repository)" : "default-simple (delivers code)"}
                  </option>
                  {offerts.map((entry) => (
                    <option key={`${entry.source}/${entry.name}`} value={`${entry.source}/${entry.name}`}>
                      {entry.title} — {entry.source}
                      {entry.agents.some((a) => a.resolution === "unresolved") ? " (an agent is missing)" : ""}
                    </option>
                  ))}
                </Select>
                <span className="block text-xs text-ink-muted">
                  From the workflow catalogue; it becomes the project&apos;s default. More can be adopted later.
                </span>
              </label>
            )}
          </div>
        )}

        {step === 1 && (
          <div className="space-y-3 text-sm">
            <Field label="Identifier (slug)" value={form.slug} onChange={(value) => set("slug", value)} placeholder="billing-api" />
            {!slugValid && form.slug && <ErrorNote>Lowercase letters, digits and dashes only</ErrorNote>}
            <Field label="Display name" value={form.name} onChange={(value) => set("name", value)} placeholder="Billing API" />
            {!form.sansDepot && (
              <>
                <Field label="Repository" value={form.repo} onChange={(value) => set("repo", value)} placeholder="acme/billing-api" />
                {!repoValid && form.repo && <ErrorNote>Expected: `owner/repo` or an https URL</ErrorNote>}
                <label className="block space-y-1">
                  <span>Main language</span>
                  <Select
                    value={form.language}
                    onChange={(event) => set("language", event.target.value)}
                    className="w-full"
                  >
                    {["python", "node", "go", "java", "dotnet", "other"].map((language) => (
                      <option key={language} value={language}>
                        {language}
                      </option>
                    ))}
                  </Select>
                </label>
              </>
            )}
          </div>
        )}

        {step === 2 && (
          <div className="space-y-3 text-sm">
            <label className="block space-y-1">
              <span>Tracker (where tickets come from)</span>
              <Select
                value={form.tracker}
                onChange={(event) => set("tracker", event.target.value)}
                className="w-full"
              >
                {TRACKERS.map((tracker) => (
                  <option key={tracker.value} value={tracker.value}>
                    {tracker.label}
                  </option>
                ))}
              </Select>
            </label>
            {!form.sansDepot && (
              <Field
                label="GitOps repository (environments)"
                value={form.gitops}
                onChange={(value) => set("gitops", value)}
                placeholder="https://github.com/acme/billing-api-gitops.git"
              />
            )}
            <Field label="Slack channel" value={form.channel} onChange={(value) => set("channel", value)} placeholder="#choregos" />
            <p className="text-xs text-ink-muted">
              Secrets are not typed here: they come from External Secrets, referenced by the connector.
              The other connectors (SCM, CI, CD, gateway) are set in the project settings.
            </p>
          </div>
        )}

        {step === 3 && (
          <dl className="space-y-2 text-sm" data-testid="recapitulatif">
            {recapitulatif.map(([libelle, valeur]) => (
              <div key={libelle} className="flex justify-between gap-4">
                <dt className="text-ink-muted">{libelle}</dt>
                <dd className="min-w-0 break-all text-right">{valeur || "—"}</dd>
              </div>
            ))}
          </dl>
        )}

        {error && (
          <ErrorNote>
            {error}
            {fait.projet && (
              <>
                {" "}
                — retrying goes on from there, or <Link href={`/p/${fait.projet.slug}`}>open the project</Link>.
              </>
            )}
          </ErrorNote>
        )}

        <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
          <Button
            onClick={() => setStep((current) => Math.max(0, current - 1))}
            // Le projet existe : revenir changer son identifiant ne le renommerait pas.
            disabled={step === 0 || fait.projet !== null}
          >
            Previous
          </Button>
          {raisonDAttente && (
            <p className="ml-auto text-xs text-ink-muted" id="raison-d-attente">
              {raisonDAttente}
            </p>
          )}
          {step < STEPS.length - 1 ? (
            <Button
              tone="primary"
              onClick={() => setStep((current) => current + 1)}
              disabled={raisonDAttente !== null}
              aria-describedby={raisonDAttente ? "raison-d-attente" : undefined}
            >
              Next
            </Button>
          ) : (
            <Button tone="primary" onClick={create} disabled={busy || !slugValid || !repoValid}>
              {fait.projet ? "Retry" : avecTemplate ? "Create and provision" : "Create"}
            </Button>
          )}
        </div>
      </Card>
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  return (
    <label className="block space-y-1">
      <span>{label}</span>
      <Input
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        className="w-full"
      />
    </label>
  );
}
