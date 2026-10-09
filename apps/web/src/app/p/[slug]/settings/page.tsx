// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import dynamic from "next/dynamic";
import { use, useState } from "react";
import { Connecteurs } from "@/components/connecteurs";
import { OperationsDuProjet } from "@/components/operations-du-projet";
import { Button, Card, Empty, ErrorNote, EtatDeLecture } from "@/components/ui";
import { api } from "@/lib/api";
import { eur, usd } from "@/lib/format";
import type { ProjectModels } from "@/lib/types";

const YamlEditor = dynamic(() => import("@/components/yaml-editor").then((m) => m.YamlEditor), {
  ssr: false,
  loading: () => <p className="text-sm text-ink-muted">loading the editor…</p>,
});

const PROFILS = ["standard", "strong", "cheap", "by_size"];

/**
 * Les paramètres d'un projet — et ils se MODIFIENT ici.
 *
 * Avant le 2026-09-24 cet écran listait les connecteurs sans pouvoir en poser un (la
 * documentation disait pourtant « Settings → Connectors »), montrait la politique dans un
 * `<pre>` et n'avait pas de réglage de modèles. Tout ce que l'API acceptait déjà se fait
 * maintenant d'ici : connecteurs (ceux que les workflows exigent, formulaires tirés du schéma de
 * leur type, secrets en références — ADR 0034), politique (YAML, validée par le serveur à
 * l'enregistrement), profils de modèles.
 */
export default function SettingsPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const queryClient = useQueryClient();
  const policy = useQuery({ queryKey: ["policy", slug], queryFn: () => api.policy(slug) });
  const matrix = useQuery({ queryKey: ["matrix", slug], queryFn: () => api.modelMatrix(slug) });
  const tools = useQuery({ queryKey: ["project-tools", slug], queryFn: () => api.projectTools(slug) });
  const models = useQuery({ queryKey: ["models", slug], queryFn: () => api.models(slug) });
  const platformModels = useQuery({ queryKey: ["platform-models"], queryFn: () => api.platformModels() });
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function faire(action: () => Promise<unknown>, succes: string, cles: string[]) {
    setError(null);
    setMessage(null);
    try {
      await action();
      setMessage(succes);
      for (const cle of cles) void queryClient.invalidateQueries({ queryKey: [cle, slug] });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "refused");
    }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {(message || error) && (
        <div className="lg:col-span-2">
          {message && <p className="text-sm text-ok">{message}</p>}
          {error && <ErrorNote>{error}</ErrorNote>}
        </div>
      )}

      <Connecteurs slug={slug} />
      <OperationsDuProjet slug={slug} />

      <Card title="policy" className="lg:col-span-2">
        <PolicyEditor
          yaml={policy.data?.yaml ?? ""}
          onSave={(yaml) => faire(() => api.putPolicy(slug, yaml), "policy saved", ["policy"])}
        />
      </Card>

      <Card title="model profiles">
        <ModelsEditor
          models={models.data}
          disponibles={(platformModels.data ?? []).map((m) => m.model_name)}
          onSave={(body) => faire(() => api.putModels(slug, body), "profiles saved", ["models", "matrix"])}
        />
      </Card>

      <Card title="tool catalogue">
        {(tools.data?.tools ?? []).length === 0 ? (
          <Empty>no tool declared by the deployment.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table>
              <thead>
                <tr>
                  <th>tool</th>
                  <th>provider</th>
                  <th>open to</th>
                  <th>per call</th>
                  <th>this project</th>
                </tr>
              </thead>
              <tbody>
                {(tools.data?.tools ?? []).map((tool) => (
                  <tr key={tool.name}>
                    <td>
                      <span className="font-mono text-xs">{tool.name}</span>
                      <p className="text-xs text-ink-muted">{tool.description}</p>
                    </td>
                    <td className="text-xs">
                      {tool.provider}
                      {tool.source === "mcp" ? " · remote MCP" : ""}
                      {tool.needs_credential ? " · platform key" : " · no key"}
                    </td>
                    <td className="text-xs">{(tool.groups ?? []).join(", ") || "everyone"}</td>
                    <td className="text-xs">{eur(tool.price_eur ?? 0)}</td>
                    <td className="text-xs">{tool.allowed ? "allowed" : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {tools.data?.allows_all && (
          <p className="mt-2 text-xs text-ink-muted">
            this project declares <span className="font-mono">*</span>: the whole catalogue is open to it.
          </p>
        )}
        <p className="mt-2 text-xs text-ink-muted">
          a project&apos;s tool list (`tools`, `groups`) is set in its configuration — it is reviewed like code.
        </p>
      </Card>

      <Card title="backend × model matrix" className="lg:col-span-2">
        <p className="mb-2 text-sm text-ink-muted">
          Published by the nightly evals: an unvalidated combination is refused on save.
        </p>
        <div className="overflow-x-auto">
          <table>
            <thead>
              <tr>
                <th>backend</th>
                <th>model</th>
                <th>validated</th>
                <th className="text-right">success</th>
                <th className="text-right">median cost</th>
                <th>memory</th>
              </tr>
            </thead>
            <tbody>
              {(matrix.data?.entries ?? []).map((entry, index) => (
                <tr key={index}>
                  <td>{entry.backend}</td>
                  <td className="font-mono text-xs">{entry.model}</td>
                  <td>{entry.validated ? "✓" : <span className="text-danger">✗</span>}</td>
                  <td className="text-right">
                    {entry.success_rate != null ? `${Math.round(entry.success_rate * 100)} %` : "—"}
                  </td>
                  <td className="text-right">
                    {entry.median_cost_usd != null ? usd(entry.median_cost_usd) : "—"}
                  </td>
                  <td>{entry.with_memory == null ? "—" : entry.with_memory ? "with" : "without"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <EtatDeLecture lecture={matrix} quoi="the eval matrix" />
        {matrix.data && (matrix.data.entries ?? []).length === 0 && <Empty>no eval published yet</Empty>}
      </Card>
    </div>
  );
}

function PolicyEditor({ yaml, onSave }: { yaml: string; onSave: (yaml: string) => Promise<void> }) {
  const [texte, setTexte] = useState("");
  const [seeded, setSeeded] = useState<string | null>(null);
  if (yaml && yaml !== seeded) {
    setSeeded(yaml);
    setTexte(yaml);
  }
  return (
    <div className="space-y-2">
      <YamlEditor label="policy YAML" value={texte} onChange={setTexte} issues={[]} />
      <div className="flex justify-end">
        <Button tone="primary" onClick={() => void onSave(texte)} disabled={!texte || texte === yaml}>
          save the policy
        </Button>
      </div>
      <p className="text-xs text-ink-muted">
        budgets, approvals, attempts, scope: the same policy the orchestrator applies. The server validates it on save.
      </p>
    </div>
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
          <input
            list="modeles-disponibles"
            value={profils[profil] ?? ""}
            placeholder={models?.inherited?.[profil] ? `inherited: ${models.inherited[profil]}` : "platform/standard"}
            onChange={(event) => setProfils((p) => ({ ...p, [profil]: event.target.value }))}
            className="w-64 min-w-0 rounded border border-line bg-surface px-2 py-1 font-mono text-xs"
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
        accept a backend × model combination not validated by the evals
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
          save the profiles
        </Button>
      </div>
    </div>
  );
}
