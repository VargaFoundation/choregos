"use client";

import { cn } from "@/lib/cn";
import { Heading, Numeral } from "@varga/design-system";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button, Card, ErrorNote } from "@/components/ui";
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
  const [step, setStep] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    template: "",
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

  async function create() {
    setBusy(true);
    setError(null);
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
      const project = await api.createProject(org, {
        slug: form.slug,
        name: form.name || form.slug,
        template_ref: avecTemplate ? form.template : null,
        config,
      });
      await api.putConnector(project.id, "tracker", { type: form.tracker, config: {} });
      if (avecTemplate) await api.provision(project.id);
      router.push(`/p/${project.slug}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "creation refused");
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <Heading as="h1" size="xl">
        new project
      </Heading>
      <ol className="grid grid-cols-4 border border-line">
        {STEPS.map((label, index) => (
          <li
            key={label}
            aria-current={index === step ? "step" : undefined}
            className={cn(
              "flex items-center gap-3 p-3 text-xs",
              index > 0 && "border-l border-line",
              index === step ? "text-ink" : "text-ink-muted",
            )}
          >
            <Numeral
              value={index + 1}
              className={cn("size-7", index > step && "border border-line-strong bg-surface text-ink-muted")}
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
              <span>this project has no code repository (a line of business: HR, purchasing, legal…)</span>
            </label>
            {!form.sansDepot && (
              <label className="block space-y-1">
                <span>stack template</span>
                <select
                  value={form.template}
                  onChange={(event) => set("template", event.target.value)}
                  className="w-full rounded border border-line bg-surface px-2 py-1.5"
                >
                  <option value="">none — I wire my connectors myself</option>
                  {(templates.data ?? []).map((template) => (
                    <option key={`${template.name}@${template.version}`} value={`${template.name}@${template.version}`}>
                      {template.display}
                      {template.is_published ? "" : " (unpublished)"}
                    </option>
                  ))}
                </select>
                <span className="block text-xs text-ink-muted">
                  A template installs the labels, the board, the webhooks, the namespaces and the CI.
                </span>
              </label>
            )}
          </div>
        )}

        {step === 1 && (
          <div className="space-y-3 text-sm">
            <Field label="identifier (slug)" value={form.slug} onChange={(value) => set("slug", value)} placeholder="billing-api" />
            {!slugValid && form.slug && <ErrorNote>lowercase letters, digits and dashes only</ErrorNote>}
            <Field label="display name" value={form.name} onChange={(value) => set("name", value)} placeholder="Billing API" />
            {!form.sansDepot && (
              <>
                <Field label="repository" value={form.repo} onChange={(value) => set("repo", value)} placeholder="varga/billing-api" />
                {!repoValid && form.repo && <ErrorNote>expected: `owner/repo` or an https URL</ErrorNote>}
                <label className="block space-y-1">
                  <span>main language</span>
                  <select
                    value={form.language}
                    onChange={(event) => set("language", event.target.value)}
                    className="w-full rounded border border-line bg-surface px-2 py-1.5"
                  >
                    {["python", "node", "go", "java", "dotnet", "other"].map((language) => (
                      <option key={language} value={language}>
                        {language}
                      </option>
                    ))}
                  </select>
                </label>
              </>
            )}
          </div>
        )}

        {step === 2 && (
          <div className="space-y-3 text-sm">
            <label className="block space-y-1">
              <span>tracker (where tickets come from)</span>
              <select
                value={form.tracker}
                onChange={(event) => set("tracker", event.target.value)}
                className="w-full rounded border border-line bg-surface px-2 py-1.5"
              >
                {TRACKERS.map((tracker) => (
                  <option key={tracker.value} value={tracker.value}>
                    {tracker.label}
                  </option>
                ))}
              </select>
            </label>
            {!form.sansDepot && (
              <Field
                label="GitOps repository (environments)"
                value={form.gitops}
                onChange={(value) => set("gitops", value)}
                placeholder="https://github.com/varga/billing-api-gitops.git"
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
          <dl className="space-y-2 text-sm">
            {Object.entries(form).map(([key, value]) => (
              <div key={key} className="flex justify-between gap-4">
                <dt className="text-ink-muted">{key}</dt>
                <dd className="font-mono text-xs">{String(value) || "—"}</dd>
              </div>
            ))}
            <div className="flex justify-between gap-4">
              <dt className="text-ink-muted">organisation</dt>
              <dd className="font-mono text-xs">{org}</dd>
            </div>
          </dl>
        )}

        {error && <ErrorNote>{error}</ErrorNote>}

        <div className="mt-4 flex justify-between">
          <Button onClick={() => setStep((current) => Math.max(0, current - 1))} disabled={step === 0}>
            previous
          </Button>
          {step < STEPS.length - 1 ? (
            <Button
              tone="primary"
              onClick={() => setStep((current) => current + 1)}
              disabled={step === 1 && (!slugValid || !repoValid)}
            >
              next
            </Button>
          ) : (
            <Button tone="primary" onClick={create} disabled={busy || !slugValid || !repoValid}>
              {avecTemplate ? "create and provision" : "create"}
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
      <input
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded border border-line bg-surface px-2 py-1.5"
      />
    </label>
  );
}
