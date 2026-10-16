// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { EvalMatrixTable } from "@/components/eval-matrix";
import { SettingsSection, useSectionWrites } from "@/components/project-settings/section";
import { Button, Card, Disclosure, Empty, EtatDeLecture, Input } from "@/components/ui";
import { api } from "@/lib/api";
import type { ProjectModels } from "@/lib/types";

const PROFILS = ["standard", "strong", "cheap", "by_size"];

/** The models the project's agents call, and the combinations the nightly evals have validated. */
export function AgentsModelsSection({ slug }: { slug: string }) {
  const matrix = useQuery({ queryKey: ["matrix", slug], queryFn: () => api.modelMatrix(slug) });
  const models = useQuery({ queryKey: ["models", slug], queryFn: () => api.models(slug) });
  const platformModels = useQuery({ queryKey: ["platform-models"], queryFn: () => api.platformModels() });
  const { faire, notice } = useSectionWrites(slug);
  return (
    <SettingsSection
      id="agents-models"
      title="Agents & models"
      lead="Which model each profile calls. The agent runtime itself is a connector, in Connections."
    >
      {notice}
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
