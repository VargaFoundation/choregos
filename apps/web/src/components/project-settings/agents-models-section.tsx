// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { EvalMatrixTable } from "@/components/eval-matrix";
import { SettingsSection, useSectionWrites } from "@/components/project-settings/section";
import { Button, Card, Disclosure, Empty, ErrorNote, EtatDeLecture, Field, Input, Select } from "@/components/ui";
import { api } from "@/lib/api";
import { useProjectConfigPatch, type ProjectConfigValue } from "@/lib/project-config";
import { useSession } from "@/lib/session";
import { usableModels } from "@/lib/usable-models";
import type { AgentBackendInfo, ProjectDto, ProjectModels } from "@/lib/types";

const PROFILS = ["standard", "strong", "cheap", "by_size"];

/** The models the project's agents call, and the combinations the nightly evals have validated. */
export function AgentsModelsSection({ slug }: { slug: string }) {
  const matrix = useQuery({ queryKey: ["matrix", slug], queryFn: () => api.modelMatrix(slug) });
  const models = useQuery({ queryKey: ["models", slug], queryFn: () => api.models(slug) });
  const { org } = useSession();
  // The models the organisation lets agents use (`/admin/models`, ADR 0060), the gateway's list when it has none.
  const platformModels = useQuery({ queryKey: ["usable-models", org], queryFn: () => usableModels(org, { usage: "agent" }) });
  const { faire, notice } = useSectionWrites(slug);
  return (
    <SettingsSection
      id="agents-models"
      title="Agents & models"
      lead="Which model each profile calls. The agent runtime itself is a connector, in Connections."
    >
      {notice}
      <BackendCard slug={slug} />
      <Card title="Model profiles">
        <ModelsEditor
          models={models.data}
          disponibles={(platformModels.data ?? []).map((m) => m.model_name)}
          onSave={(body) => faire(() => api.putModels(slug, body), "Profiles saved", ["models", "matrix"])}
        />
      </Card>
      <Disclosure
        title="Evaluated combinations"
        summary={`${(matrix.data?.entries ?? []).length} published by the nightly evals`}
      >
        <div className="space-y-2 p-4 pt-0">
          <p className="text-sm text-ink-muted">
            Published by the nightly evals: an unvalidated combination is refused on save.
          </p>
          <EvalMatrixTable entries={matrix.data?.entries ?? []} />
          <EtatDeLecture lecture={matrix} quoi="the eval matrix" />
          {matrix.data && (matrix.data.entries ?? []).length === 0 && <Empty>No eval published yet</Empty>}
        </div>
      </Disclosure>
    </SettingsSection>
  );
}

function ModelsEditor({
  models,
  disponibles,
  onSave,
}: {
  models?: ProjectModels;
  disponibles: string[];
  onSave: (body: ProjectModels) => Promise<void>;
}) {
  const [profils, setProfils] = useState<Record<string, string>>({});
  const [seeded, setSeeded] = useState<ProjectModels | null>(null);
  const [allowUnvalidated, setAllowUnvalidated] = useState(false);
  if (models && models !== seeded) {
    setSeeded(models);
    setProfils(Object.fromEntries(Object.entries(models.profiles ?? {}).map(([k, v]) => [k, v.litellm_model])));
    setAllowUnvalidated(models.allow_unvalidated ?? false);
  }
  const options = Array.from(new Set([...disponibles, ...Object.values(profils)])).filter(Boolean);
  return (
    <div className="space-y-2 text-sm">
      {PROFILS.map((profil) => (
        <label key={profil} className="flex items-center justify-between gap-3">
          <span className="font-mono text-xs">profile:{profil}</span>
          <Input
            list="modeles-disponibles"
            value={profils[profil] ?? ""}
            placeholder={models?.inherited?.[profil] ? `Inherited: ${models.inherited[profil]}` : "platform/standard"}
            onChange={(event) => setProfils((p) => ({ ...p, [profil]: event.target.value }))}
            className="w-64 min-w-0 font-mono"
          />
        </label>
      ))}
      <datalist id="modeles-disponibles">
        {options.map((m) => (
          <option key={m} value={m} />
        ))}
      </datalist>
      <label className="flex items-center gap-2 text-xs text-ink-muted">
        <input type="checkbox" checked={allowUnvalidated} onChange={(e) => setAllowUnvalidated(e.target.checked)} />
        Accept a backend × model combination not validated by the evals
      </label>
      <div className="flex justify-end">
        <Button
          tone="primary"
          onClick={() =>
            void onSave({
              profiles: Object.fromEntries(
                Object.entries(profils)
                  .filter(([, v]) => v)
                  .map(([k, v]) => [k, { litellm_model: v, params: {}, max_turns_factor: 1 }]),
              ),
              allow_unvalidated: allowUnvalidated,
              inherited: {},
            })
          }
        >
          Save the profiles
        </Button>
      </div>
    </div>
  );
}

/** The backends this deployment runs: a project picks its default among them, never outside them. */
export function usableBackends(backends: AgentBackendInfo[]): string[] {
  return backends.filter((b) => b.enabled).map((b) => b.name);
}

/** Why a default/allowed pair cannot be saved, or null. */
export function backendProblem(defaultBackend: string, allowed: string[]): string | null {
  if (allowed.length > 0 && !allowed.includes(defaultBackend)) return "The default backend must be one of the allowed ones.";
  return null;
}

function BackendCard({ slug }: { slug: string }) {
  const project = useQuery({ queryKey: ["project", slug], queryFn: () => api.project(slug) });
  const backends = useQuery({ queryKey: ["backends"], queryFn: () => api.backends() });
  if (!project.data) return null;
  return (
    <BackendForm
      key={JSON.stringify((project.data.config as ProjectConfigValue).agent ?? {})}
      slug={slug}
      project={project.data}
      backends={backends.data ?? []}
      loading={backends.isLoading}
      failed={backends.error ? String(backends.error) : null}
    />
  );
}

function BackendForm({
  slug,
  project,
  backends,
  loading,
  failed,
}: {
  slug: string;
  project: ProjectDto;
  backends: AgentBackendInfo[];
  loading: boolean;
  failed: string | null;
}) {
  const agent = (project.config as ProjectConfigValue).agent ?? {};
  const { patch, pending, message, error } = useProjectConfigPatch(slug);
  const [defaultBackend, setDefaultBackend] = useState(agent.default_backend ?? "claude-code");
  const [allowed, setAllowed] = useState<string[]>(agent.allowed_backends ?? []);
  const usable = usableBackends(backends);
  const options = Array.from(new Set([...usable, defaultBackend]));
  const problem = backendProblem(defaultBackend, allowed);
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void patch((latest) => ({ agent: { ...latest.agent, default_backend: defaultBackend, allowed_backends: allowed } }), {
          saved: "Backend saved",
        });
      }}
    >
      <Card title="Agent backend">
        <div className="grid gap-3 md:grid-cols-2">
          <Field label="Default backend" hint="The backends this deployment runs. The runtime itself is a connector.">
            {(p) => (
              <Select {...p} value={defaultBackend} onChange={(e) => setDefaultBackend(e.target.value)}>
                {options.map((name) => (
                  <option key={name} value={name}>
                    {name}
                    {usable.includes(name) || loading ? "" : " (not run by this deployment)"}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <fieldset className="space-y-1">
            <legend className="text-xs text-ink-muted">Allowed backends (none ticked: the deployment&apos;s list)</legend>
            {usable.map((name) => (
              <label key={name} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={allowed.includes(name)}
                  onChange={(e) => setAllowed(e.target.checked ? [...allowed, name] : allowed.filter((n) => n !== name))}
                />
                {name}
              </label>
            ))}
          </fieldset>
        </div>
        {failed && <ErrorNote>{failed}</ErrorNote>}
        {problem && <p className="mt-2 text-xs text-danger">{problem}</p>}
        <div className="mt-3 flex flex-wrap items-center justify-end gap-3">
          {message && (
            <p className="text-sm text-ok" role="status">
              {message}
            </p>
          )}
          {error && <ErrorNote>{error}</ErrorNote>}
          <Button tone="primary" type="submit" disabled={pending || problem !== null}>
            Save backend
          </Button>
        </div>
      </Card>
    </form>
  );
}
