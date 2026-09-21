"use client";

import { cn } from "@/lib/cn";
import { Heading, Numeral } from "@varga/design-system";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button, Card, ErrorNote } from "@/components/ui";
import { api, DEFAULT_ORG } from "@/lib/api";

const STEPS = ["template", "dépôt", "connecteurs", "récapitulatif"];

/** Wizard de création : template → dépôt → connecteurs → récapitulatif → provisioning. */
export default function NewProjectPage() {
  const router = useRouter();
  const [step, setStep] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    template: "github-tekton-argo-k8s@1.0.0",
    slug: "",
    name: "",
    repo: "",
    language: "python",
    gitops: "",
    channel: "#choregos",
  });

  function set<K extends keyof typeof form>(key: K, value: (typeof form)[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  const slugValid = /^[a-z0-9][a-z0-9-]{0,62}$/.test(form.slug);
  const repoValid = form.repo.startsWith("http") || /^[\w.-]+\/[\w.-]+$/.test(form.repo);

  async function create() {
    setBusy(true);
    setError(null);
    try {
      const project = await api.createProject(DEFAULT_ORG, {
        slug: form.slug,
        name: form.name || form.slug,
        template_ref: form.template,
        config: {
          slug: form.slug,
          org: DEFAULT_ORG,
          repo: {
            url: form.repo.startsWith("http") ? form.repo : `https://github.com/${form.repo}.git`,
            default_branch: "main",
            language: form.language,
          },
          gitops: form.gitops ? { repo_url: form.gitops, apps: [form.slug] } : undefined,
          notify: { slack_channel: form.channel },
        },
      });
      await api.provision(project.id);
      router.push(`/p/${project.slug}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "création refusée");
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <Heading as="h1" size="xl">
        nouveau projet
      </Heading>
      {/* Les étapes avec le carré numéroté de la fondation : plein pour l'étape courante et
          celles franchies, au filet pour celles qui restent. */}
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
          <label className="block space-y-1 text-sm">
            <span>template de stack</span>
            <select
              value={form.template}
              onChange={(event) => set("template", event.target.value)}
              className="w-full rounded border border-line bg-surface px-2 py-1.5"
            >
              <option value="github-tekton-argo-k8s@1.0.0">GitHub · Tekton · Argo CD · Kubernetes</option>
            </select>
            <span className="block text-xs text-ink-muted">
              Le template installe les labels, le board, les webhooks, les namespaces et la CI.
            </span>
          </label>
        )}

        {step === 1 && (
          <div className="space-y-3 text-sm">
            <Field label="identifiant (slug)" value={form.slug} onChange={(value) => set("slug", value)} placeholder="billing-api" />
            {!slugValid && form.slug && <ErrorNote>minuscules, chiffres et tirets uniquement</ErrorNote>}
            <Field label="nom affiché" value={form.name} onChange={(value) => set("name", value)} placeholder="Billing API" />
            <Field label="dépôt" value={form.repo} onChange={(value) => set("repo", value)} placeholder="varga/billing-api" />
            {!repoValid && form.repo && <ErrorNote>attendu : `owner/repo` ou une URL https</ErrorNote>}
            <label className="block space-y-1">
              <span>langage principal</span>
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
          </div>
        )}

        {step === 2 && (
          <div className="space-y-3 text-sm">
            <Field
              label="dépôt GitOps (environnements)"
              value={form.gitops}
              onChange={(value) => set("gitops", value)}
              placeholder="https://github.com/varga/billing-api-gitops.git"
            />
            <Field label="canal Slack" value={form.channel} onChange={(value) => set("channel", value)} placeholder="#choregos" />
            <p className="text-xs text-ink-muted">
              Les secrets ne sont pas saisis ici : ils viennent d&apos;External Secrets, référencés par le connecteur.
            </p>
          </div>
        )}

        {step === 3 && (
          <dl className="space-y-2 text-sm">
            {Object.entries(form).map(([key, value]) => (
              <div key={key} className="flex justify-between gap-4">
                <dt className="text-ink-muted">{key}</dt>
                <dd className="font-mono text-xs">{value || "—"}</dd>
              </div>
            ))}
          </dl>
        )}

        {error && <ErrorNote>{error}</ErrorNote>}

        <div className="mt-4 flex justify-between">
          <Button onClick={() => setStep((current) => Math.max(0, current - 1))} disabled={step === 0}>
            précédent
          </Button>
          {step < STEPS.length - 1 ? (
            <Button
              tone="primary"
              onClick={() => setStep((current) => current + 1)}
              disabled={step === 1 && (!slugValid || !repoValid)}
            >
              suivant
            </Button>
          ) : (
            <Button tone="primary" onClick={create} disabled={busy || !slugValid || !repoValid}>
              créer et provisionner
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
