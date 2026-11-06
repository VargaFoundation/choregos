// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { SettingsSection } from "@/components/project-settings/section";
import { Button, Card, ErrorNote, Field, Input, Select } from "@/components/ui";
import { api } from "@/lib/api";
import { useProjectConfigPatch, type ProjectConfigValue } from "@/lib/project-config";
import { nameOfUrl } from "@/lib/repositories";
import type { ProjectDto, TemplateSummary } from "@/lib/types";

const LANGUAGES = ["python", "node", "go", "java", "dotnet", "rust", "other"] as const;
type Language = (typeof LANGUAGES)[number];
type Row = { key: string; value: string };

/** `1.2.3` against `1.10.0`, numerically. */
function newerThan(a: string, b: string): boolean {
  const [x, y] = [a, b].map((v) => v.split(".").map((n) => Number.parseInt(n, 10) || 0));
  for (let i = 0; i < 3; i += 1) if ((x?.[i] ?? 0) !== (y?.[i] ?? 0)) return (x?.[i] ?? 0) > (y?.[i] ?? 0);
  return false;
}

/** The version of the project's template the manifest now offers, when it is newer than the one in use. */
export function newerTemplateVersion(templateRef: string | null | undefined, templates: TemplateSummary[]): string | null {
  const [name, version] = (templateRef ?? "").split("@");
  if (!name || !version) return null;
  const offered = templates.filter((t) => t.name === name && t.is_published && newerThan(t.version, version));
  return offered.sort((a, b) => (newerThan(a.version, b.version) ? -1 : 1))[0]?.version ?? null;
}

const toRows = (record: Record<string, string> | undefined): Row[] => Object.entries(record ?? {}).map(([key, value]) => ({ key, value }));
const fromRows = (rows: Row[]): Record<string, string> =>
  Object.fromEntries(rows.filter((r) => r.key.trim()).map((r) => [r.key.trim(), r.value]));
const blank = (value: string): string | undefined => (value.trim() === "" ? undefined : value.trim());

type NamedRepository = NonNullable<ProjectConfigValue["repositories"]>[number];
/** Another repository of the project (ADR 0124), as the card edits it; the fields it does not show are kept. */
type RepoRow = {
  name: string;
  url: string;
  branch: string;
  test: string;
  lint: string;
  typecheck: string;
  rest: Partial<NamedRepository>;
};
const SLUG = /^[a-z0-9][a-z0-9-]{0,62}$/;

const toRepoRows = (list: NamedRepository[] | undefined): RepoRow[] =>
  (list ?? []).map((r) => ({
    name: r.name,
    url: r.url,
    branch: r.default_branch ?? "main",
    test: r.test_command ?? "",
    lint: r.lint_command ?? "",
    typecheck: r.typecheck_command ?? "",
    rest: r,
  }));
const fromRepoRows = (rows: RepoRow[]): NamedRepository[] =>
  rows.map((r) => ({
    ...r.rest,
    name: r.name.trim(),
    url: r.url.trim(),
    default_branch: blank(r.branch) ?? "main",
    test_command: blank(r.test),
    lint_command: blank(r.lint),
    typecheck_command: blank(r.typecheck),
  }));

/** Why the repositories cannot be saved as they are, or `null`: the API refuses the same. */
export function repositoriesProblem(primary: string | null, rows: RepoRow[]): string | null {
  if (rows.length === 0) return null;
  if (primary === null) return "Set the primary repository's URL: the other repositories need a primary.";
  if (rows.some((r) => !r.url.trim())) return "Each repository needs its URL.";
  if (rows.some((r) => !SLUG.test(r.name.trim())))
    return "Each repository needs a name: lowercase letters, digits and dashes.";
  const names = [primary, ...rows.map((r) => r.name.trim())];
  const twice = names.find((name, index) => names.indexOf(name) !== index);
  return twice ? `Two repositories are named ${twice}: a ticket names one.` : null;
}

/** The project's name, repository, measured checks, environments, labels and definition-of-done facts. */
export function GeneralSection({ slug }: { slug: string }) {
  const project = useQuery({ queryKey: ["project", slug], queryFn: () => api.project(slug) });
  const templates = useQuery({ queryKey: ["templates"], queryFn: () => api.templates() });
  // Held here, above the form the save re-seeds: the form remounts with the saved data, and its
  // "saved" line must survive it.
  const saving = useProjectConfigPatch(slug);
  return (
    <SettingsSection
      id="general"
      title="General"
      lead="The project's identity, the repository the runs work in, and the checks the runner measures itself rather than believing the agent."
    >
      {project.error && <ErrorNote>{String(project.error)}</ErrorNote>}
      {project.data && <ProjectActivation project={project.data} slug={slug} />}
      {project.data && (
        // Re-seeded from the server after each save, never while a person is typing.
        <GeneralForm
          key={`${project.data.name}|${JSON.stringify(project.data.config)}`}
          project={project.data}
          templates={templates.data ?? []}
          saving={saving}
        />
      )}
    </SettingsSection>
  );
}

/** What the button does for a project that is not active: none when there is nothing to do here. */
export function activationGesture(project: Pick<ProjectDto, "status" | "template_ref" | "config">): string | null {
  const status = project.status;
  if (status === "active" || status === "archived") return null;
  if (status === "provisioning") return "Provisioning…";
  if (status === "suspended") return "Resume";
  const repo = (project.config as ProjectConfigValue | undefined)?.repo?.url;
  // Without a template or a repository, there is nothing to provision: the API activates it at once.
  return project.template_ref || repo ? "Provision" : "Activate";
}

/** A project that is not active runs nothing; this is where it becomes active. */
function ProjectActivation({ project, slug }: { project: ProjectDto; slug: string }) {
  const client = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const gesture = activationGesture(project);
  if (gesture === null) return null;
  async function activate() {
    setBusy(true);
    setError(null);
    try {
      await api.provision(slug);
      await Promise.all([
        client.invalidateQueries({ queryKey: ["project", slug] }),
        client.invalidateQueries({ queryKey: ["readiness", slug] }),
        client.invalidateQueries({ queryKey: ["provision", slug] }),
      ]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "refused");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card title="Status">
      <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
        <p className="min-w-0 flex-1" data-testid="project-status">
          The project is <strong>{project.status}</strong>: nothing runs until it is active.
          {gesture === "Provision" && " Provisioning runs its template's steps; the overview follows them."}
        </p>
        <Button tone="accent" onClick={() => void activate()} disabled={busy || project.status === "provisioning"}>
          {gesture}
        </Button>
      </div>
      {error && <ErrorNote>{error}</ErrorNote>}
    </Card>
  );
}

function GeneralForm({
  project,
  templates,
  saving,
}: {
  project: ProjectDto;
  templates: TemplateSummary[];
  saving: ReturnType<typeof useProjectConfigPatch>;
}) {
  const config = project.config as ProjectConfigValue;
  const { patch, pending, message, error } = saving;
  const [name, setName] = useState(project.name);
  const [envs, setEnvs] = useState<string[]>(config.envs ?? []);
  const [newEnv, setNewEnv] = useState("");
  const [labels, setLabels] = useState<Row[]>(toRows(config.labels));
  const [url, setUrl] = useState(config.repo?.url ?? "");
  const [branch, setBranch] = useState(config.repo?.default_branch ?? "main");
  const [language, setLanguage] = useState<string>(config.repo?.language ?? "");
  const [prefix, setPrefix] = useState(config.repo?.branch_prefix ?? "");
  const [depth, setDepth] = useState(config.repo?.clone_depth ? String(config.repo.clone_depth) : "");
  const [testCommand, setTestCommand] = useState(config.repo?.test_command ?? "");
  const [lintCommand, setLintCommand] = useState(config.repo?.lint_command ?? "");
  const [typecheckCommand, setTypecheckCommand] = useState(config.repo?.typecheck_command ?? "");
  const [repoName, setRepoName] = useState(config.repo?.name ?? "");
  const [others, setOthers] = useState<RepoRow[]>(toRepoRows(config.repositories));
  const [facts, setFacts] = useState<Row[]>(toRows(config.dod?.facts));
  const newer = newerTemplateVersion(project.template_ref ?? config.template_ref, templates);
  const hasRepo = url.trim() !== "";
  const checksWithoutRepo = !hasRepo && [testCommand, lintCommand, typecheckCommand].some((c) => c.trim());
  const primary = hasRepo ? blank(repoName) ?? nameOfUrl(url) : null;
  const othersProblem = repositoriesProblem(primary, others);

  /** The other repository at `index` becomes the primary, and the primary takes its place in the list. */
  function makePrimary(index: number) {
    const row = others[index];
    if (!row || primary === null) return;
    const former: RepoRow = {
      name: primary,
      url,
      branch,
      test: testCommand,
      lint: lintCommand,
      typecheck: typecheckCommand,
      rest: {
        language: (blank(language) as Language | undefined) ?? undefined,
        branch_prefix: blank(prefix),
        clone_depth: depth.trim() ? Number.parseInt(depth, 10) : undefined,
      },
    };
    setOthers(others.map((r, i) => (i === index ? former : r)));
    setRepoName(row.name);
    setUrl(row.url);
    setBranch(row.branch);
    setTestCommand(row.test);
    setLintCommand(row.lint);
    setTypecheckCommand(row.typecheck);
    setLanguage(row.rest.language ?? "");
    setPrefix(row.rest.branch_prefix ?? "");
    setDepth(row.rest.clone_depth ? String(row.rest.clone_depth) : "");
  }

  function addEnv() {
    const env = newEnv.trim();
    if (env && !envs.includes(env)) setEnvs([...envs, env]);
    setNewEnv("");
  }

  async function save() {
    await patch(
      (latest) => ({
        envs: envs.length > 0 ? envs : undefined,
        labels: Object.keys(fromRows(labels)).length > 0 ? fromRows(labels) : undefined,
        repo: hasRepo
          ? {
              ...latest.repo,
              name: blank(repoName),
              url: url.trim(),
              default_branch: blank(branch) ?? "main",
              language: (blank(language) as Language | undefined) ?? undefined,
              branch_prefix: blank(prefix),
              clone_depth: depth.trim() ? Number.parseInt(depth, 10) : undefined,
              test_command: blank(testCommand),
              lint_command: blank(lintCommand),
              typecheck_command: blank(typecheckCommand),
            }
          : undefined,
        repositories: others.length > 0 ? fromRepoRows(others) : undefined,
        dod:
          Object.keys(fromRows(facts)).length > 0 || Object.keys(latest.dod ?? {}).some((k) => k !== "facts")
            ? { ...latest.dod, facts: fromRows(facts) }
            : undefined,
      }),
      { name: name.trim() || project.name, saved: "General settings saved" },
    );
  }

  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <Card title="Identity">
        <div className="grid gap-3 md:grid-cols-2">
          <Field label="Name">{(p) => <Input {...p} value={name} onChange={(e) => setName(e.target.value)} required />}</Field>
          <div className="space-y-1.5 text-sm">
            <span className="text-xs text-ink-muted">Template</span>
            <p className="font-mono text-xs" data-testid="template-ref">
              {project.template_ref ?? config.template_ref ?? "none"}
            </p>
            {newer && (
              <p className="text-xs text-warn" data-testid="template-newer">
                A newer version exists: {newer}.
              </p>
            )}
          </div>
        </div>
        <div className="mt-3 space-y-1.5">
          <span className="text-xs text-ink-muted">Environments</span>
          <ul className="flex flex-wrap gap-2" aria-label="Environments">
            {envs.map((env) => (
              <li key={env} className="inline-flex items-center gap-1 rounded-full border border-line px-2.5 py-0.5 text-xs">
                {env}
                <button type="button" aria-label={`Remove environment ${env}`} onClick={() => setEnvs(envs.filter((e) => e !== env))}>
                  ×
                </button>
              </li>
            ))}
          </ul>
          <div className="flex gap-2">
            <Input
              aria-label="New environment"
              value={newEnv}
              placeholder="staging"
              className="max-w-48"
              onChange={(e) => setNewEnv(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  addEnv();
                }
              }}
            />
            <Button size="sm" onClick={addEnv}>
              Add environment
            </Button>
          </div>
        </div>
        <RowsEditor title="Labels" keyLabel="Label" valueLabel="Value" rows={labels} onChange={setLabels} />
      </Card>

      <Card title="Repository">
        <div className="grid gap-3 md:grid-cols-2">
          <Field label="Repository URL" hint="Leave empty for a project without a repository.">
            {(p) => <Input {...p} value={url} placeholder="https://github.com/owner/repo.git" onChange={(e) => setUrl(e.target.value)} />}
          </Field>
          <Field label="Default branch">{(p) => <Input {...p} value={branch} disabled={!hasRepo} onChange={(e) => setBranch(e.target.value)} />}</Field>
          <Field label="Language">
            {(p) => (
              <Select {...p} value={language} disabled={!hasRepo} onChange={(e) => setLanguage(e.target.value)}>
                <option value="">—</option>
                {LANGUAGES.map((l) => (
                  <option key={l} value={l}>
                    {l}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Branch prefix" hint="Default choregos/">
            {(p) => <Input {...p} value={prefix} disabled={!hasRepo} onChange={(e) => setPrefix(e.target.value)} />}
          </Field>
          <Field label="Clone depth" hint="Default 50">
            {(p) => <Input {...p} type="number" min={1} value={depth} disabled={!hasRepo} onChange={(e) => setDepth(e.target.value)} />}
          </Field>
          <Field label="Name in the project" hint={`What a ticket's repository field names; default ${hasRepo ? nameOfUrl(url) : "its URL's last segment"}`}>
            {(p) => <Input {...p} value={repoName} disabled={!hasRepo} placeholder={hasRepo ? nameOfUrl(url) : ""} onChange={(e) => setRepoName(e.target.value)} />}
          </Field>
        </div>
      </Card>

      <Card title="Other repositories">
        <p className="mb-3 text-xs text-ink-muted">
          A ticket works on one repository: the one its <code>repository</code> field names, else the primary above. Its branch, pull request,
          checks and measured commands are that repository&apos;s. A change across several repositories is one child ticket per repository.
        </p>
        <ul className="space-y-3" aria-label="Other repositories">
          {others.map((row, index) => {
            const set = (change: Partial<RepoRow>) => setOthers(others.map((r, i) => (i === index ? { ...r, ...change } : r)));
            return (
              <li key={index} className="space-y-2 rounded-md border border-line p-3">
                <div className="grid gap-2 md:grid-cols-3">
                  <Input aria-label={`Repository name ${index + 1}`} value={row.name} placeholder="diamctl" onChange={(e) => set({ name: e.target.value })} />
                  <Input
                    aria-label={`Repository URL ${index + 1}`}
                    value={row.url}
                    className="md:col-span-2"
                    placeholder="https://github.com/owner/repo.git"
                    onChange={(e) => set({ url: e.target.value })}
                  />
                  <Input aria-label={`Default branch ${index + 1}`} value={row.branch} onChange={(e) => set({ branch: e.target.value })} />
                  <Input aria-label={`Test command ${index + 1}`} value={row.test} placeholder="test command" onChange={(e) => set({ test: e.target.value })} />
                  <Input aria-label={`Lint command ${index + 1}`} value={row.lint} placeholder="lint command" onChange={(e) => set({ lint: e.target.value })} />
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    aria-label={`Make ${row.name || `repository ${index + 1}`} the primary`}
                    onClick={() => makePrimary(index)}
                    disabled={primary === null}
                  >
                    Make primary
                  </Button>
                  <Button
                    size="sm"
                    aria-label={`Remove ${row.name || `repository ${index + 1}`}`}
                    onClick={() => setOthers(others.filter((_, i) => i !== index))}
                  >
                    Remove
                  </Button>
                </div>
              </li>
            );
          })}
        </ul>
        <div className="mt-3">
          <Button
            size="sm"
            onClick={() => setOthers([...others, { name: "", url: "", branch: "main", test: "", lint: "", typecheck: "", rest: {} }])}
          >
            Add a repository
          </Button>
        </div>
        {othersProblem && <p className="mt-2 text-xs text-warn">{othersProblem}</p>}
      </Card>

      <Card title="Measured checks">
        <p className="mb-3 text-xs text-ink-muted">
          The runner runs these after a step and writes the result itself: exit code 0 is true. They live under the repository.
        </p>
        <div className="grid gap-3 md:grid-cols-3">
          <Field label="Test command">
            {(p) => <Input {...p} value={testCommand} disabled={!hasRepo} placeholder="pytest -q" onChange={(e) => setTestCommand(e.target.value)} />}
          </Field>
          <Field label="Lint command">
            {(p) => <Input {...p} value={lintCommand} disabled={!hasRepo} placeholder="ruff check ." onChange={(e) => setLintCommand(e.target.value)} />}
          </Field>
          <Field label="Typecheck command">
            {(p) => (
              <Input {...p} value={typecheckCommand} disabled={!hasRepo} placeholder="mypy ." onChange={(e) => setTypecheckCommand(e.target.value)} />
            )}
          </Field>
        </div>
        {checksWithoutRepo && <p className="mt-2 text-xs text-warn">Set a repository URL: a check has nothing to run in without one.</p>}
        <RowsEditor
          title="Definition-of-done facts"
          hint="A fact name and the command that proves it (exit code 0 = true). Works without a repository."
          keyLabel="Fact"
          valueLabel="Command"
          rows={facts}
          onChange={setFacts}
        />
      </Card>

      <div className="flex flex-wrap items-center justify-end gap-3">
        {message && (
          <p className="text-sm text-ok" role="status">
            {message}
          </p>
        )}
        {error && <ErrorNote>{error}</ErrorNote>}
        <Button tone="primary" type="submit" disabled={pending || checksWithoutRepo || othersProblem !== null}>
          Save general settings
        </Button>
      </div>
    </form>
  );
}

function RowsEditor({
  title,
  hint,
  keyLabel,
  valueLabel,
  rows,
  onChange,
}: {
  title: string;
  hint?: string;
  keyLabel: string;
  valueLabel: string;
  rows: Row[];
  onChange: (rows: Row[]) => void;
}) {
  return (
    <fieldset className="mt-3 space-y-2">
      <legend className="text-xs text-ink-muted">{title}</legend>
      {hint && <p className="text-xs text-ink-muted">{hint}</p>}
      {rows.map((row, index) => (
        <div key={index} className="flex flex-wrap items-center gap-2">
          <Input
            aria-label={`${keyLabel} ${index + 1}`}
            value={row.key}
            className="w-40"
            onChange={(e) => onChange(rows.map((r, i) => (i === index ? { ...r, key: e.target.value } : r)))}
          />
          <Input
            aria-label={`${valueLabel} ${index + 1}`}
            value={row.value}
            className="min-w-0 flex-1 font-mono"
            onChange={(e) => onChange(rows.map((r, i) => (i === index ? { ...r, value: e.target.value } : r)))}
          />
          <Button size="sm" onClick={() => onChange(rows.filter((_, i) => i !== index))}>
            Remove
          </Button>
        </div>
      ))}
      <Button size="sm" onClick={() => onChange([...rows, { key: "", value: "" }])}>
        Add {keyLabel.toLowerCase()}
      </Button>
    </fieldset>
  );
}
