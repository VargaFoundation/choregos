"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import dynamic from "next/dynamic";
import { use, useState } from "react";
import { Button, Card, Empty, ErrorNote, StateBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { eur, shortDate } from "@/lib/format";
import type { ConnectorType, ProjectModels } from "@/lib/types";

const YamlEditor = dynamic(() => import("@/components/yaml-editor").then((m) => m.YamlEditor), {
  ssr: false,
  loading: () => <p className="text-sm text-ink-muted">éditeur en cours de chargement…</p>,
});

const KINDS = ["tracker", "scm", "ci", "cd", "runtime", "gateway", "memory", "notify"];
const PROFILS = ["standard", "strong", "cheap", "by_size"];

/**
 * Les paramètres d'un projet — et ils se MODIFIENT ici.
 *
 * Avant le 2026-09-24 cet écran listait les connecteurs sans pouvoir en poser un (la
 * documentation disait pourtant « Settings → Connectors »), montrait la politique dans un
 * `<pre>` et n'avait pas de réglage de modèles. Tout ce que l'API acceptait déjà se fait
 * maintenant d'ici : connecteur (type + configuration), politique (YAML, validée par le
 * serveur à l'enregistrement), profils de modèles.
 */
export default function SettingsPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const queryClient = useQueryClient();
  const connectors = useQuery({ queryKey: ["connectors", slug], queryFn: () => api.connectors(slug) });
  const types = useQuery({ queryKey: ["connector-types"], queryFn: () => api.connectorTypes() });
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
      setError(cause instanceof Error ? cause.message : "refusé");
    }
  }

  async function test(kind: string) {
    await faire(
      async () => {
        const result = await api.testConnector(slug, kind);
        setMessage(
          `${kind} : ${result.ok ? "connexion OK" : "échec"} — ` +
            result.checks.map((check) => `${check.name} ${check.ok ? "✓" : "✗"}`).join(", "),
        );
      },
      "",
      ["connectors"],
    );
  }

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {(message || error) && (
        <div className="lg:col-span-2">
          {message && <p className="text-sm text-ok">{message}</p>}
          {error && <ErrorNote>{error}</ErrorNote>}
        </div>
      )}

      <Card title="connecteurs" className="lg:col-span-2">
        <table>
          <thead>
            <tr>
              <th>type</th>
              <th>implémentation</th>
              <th>état</th>
              <th>dernier test</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {KINDS.map((kind) => {
              const connector = (connectors.data ?? []).find((c) => c.kind === kind);
              return (
                <ConnectorRow
                  key={kind}
                  kind={kind}
                  current={connector}
                  types={(types.data ?? []).filter((t) => t.kind === kind)}
                  onSave={(type, config) =>
                    faire(() => api.putConnector(slug, kind, { type, config }), `connecteur ${kind} enregistré`, ["connectors"])
                  }
                  onTest={connector ? () => test(kind) : undefined}
                />
              );
            })}
          </tbody>
        </table>
      </Card>

      <Card title="politique" className="lg:col-span-2">
        <PolicyEditor
          yaml={policy.data?.yaml ?? ""}
          onSave={(yaml) => faire(() => api.putPolicy(slug, yaml), "politique enregistrée", ["policy"])}
        />
      </Card>

      <Card title="profils de modèles">
        <ModelsEditor
          models={models.data}
          disponibles={(platformModels.data ?? []).map((m) => m.model_name)}
          onSave={(body) => faire(() => api.putModels(slug, body), "profils enregistrés", ["models", "matrix"])}
        />
      </Card>

      <Card title="outils du catalogue">
        {(tools.data?.tools ?? []).length === 0 ? (
          <Empty>aucun outil déclaré par le déploiement.</Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <th>outil</th>
                <th>fournisseur</th>
                <th>ouvert à</th>
                <th>par appel</th>
                <th>ce projet</th>
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
                    {tool.source === "mcp" ? " · MCP distant" : ""}
                    {tool.needs_credential ? " · clé plateforme" : " · sans clé"}
                  </td>
                  <td className="text-xs">{(tool.groups ?? []).join(", ") || "tous"}</td>
                  <td className="text-xs">{eur(tool.price_eur ?? 0)}</td>
                  <td className="text-xs">{tool.allowed ? "autorisé" : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {tools.data?.allows_all && (
          <p className="mt-2 text-xs text-ink-muted">
            ce projet déclare <span className="font-mono">*</span> : tout le catalogue lui est ouvert.
          </p>
        )}
        <p className="mt-2 text-xs text-ink-muted">
          la liste des outils d&apos;un projet (`tools`, `groups`) se règle dans sa configuration — elle est relue
          comme du code.
        </p>
      </Card>

      <Card title="matrice backend × modèle" className="lg:col-span-2">
        <p className="mb-2 text-sm text-ink-muted">
          Publiée par les évals nocturnes : une combinaison non validée est refusée à l&apos;enregistrement.
        </p>
        <table>
          <thead>
            <tr>
              <th>backend</th>
              <th>modèle</th>
              <th>validé</th>
              <th className="text-right">réussite</th>
              <th className="text-right">coût médian</th>
              <th>mémoire</th>
            </tr>
          </thead>
          <tbody>
            {(matrix.data?.entries ?? []).map((entry, index) => (
              <tr key={index}>
                <td>{entry.backend}</td>
                <td className="font-mono text-xs">{entry.model}</td>
                <td>{entry.validated ? "✓" : <span className="text-danger">✗</span>}</td>
                <td className="text-right">{entry.success_rate != null ? `${Math.round(entry.success_rate * 100)} %` : "—"}</td>
                <td className="text-right">{entry.median_cost_usd != null ? `${entry.median_cost_usd.toFixed(2)} $` : "—"}</td>
                <td>{entry.with_memory == null ? "—" : entry.with_memory ? "avec" : "sans"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {(matrix.data?.entries ?? []).length === 0 && <Empty>aucune éval publiée pour l&apos;instant</Empty>}
      </Card>
    </div>
  );
}

function ConnectorRow({
  kind,
  current,
  types,
  onSave,
  onTest,
}: {
  kind: string;
  current?: { type: string; status: string; last_check_at?: string | null; config?: Record<string, unknown> | null };
  types: ConnectorType[];
  onSave: (type: string, config: Record<string, unknown>) => Promise<void>;
  onTest?: () => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [type, setType] = useState(current?.type ?? types[0]?.type ?? "fake");
  const [config, setConfig] = useState(JSON.stringify(current?.config ?? {}, null, 2));
  const [invalide, setInvalide] = useState<string | null>(null);

  async function save() {
    let parsed: Record<string, unknown>;
    try {
      parsed = config.trim() ? (JSON.parse(config) as Record<string, unknown>) : {};
    } catch {
      setInvalide("la configuration doit être un objet JSON");
      return;
    }
    setInvalide(null);
    await onSave(type, parsed);
    setEditing(false);
  }

  return (
    <>
      <tr>
        <td>{kind}</td>
        <td className="font-mono text-xs">{current?.type ?? <span className="text-ink-muted">— non configuré</span>}</td>
        <td>
          {current && (
            <StateBadge
              state={current.status}
              display={current.status}
              kind={current.status === "ok" ? "terminal" : current.status === "error" ? "blocked" : "wait"}
            />
          )}
        </td>
        <td className="text-xs text-ink-muted">{current?.last_check_at ? shortDate(current.last_check_at) : "—"}</td>
        <td className="space-x-2 whitespace-nowrap">
          {onTest && (
            <Button size="sm" onClick={() => void onTest()}>
              tester
            </Button>
          )}
          <Button size="sm" onClick={() => setEditing((e) => !e)}>
            {current ? "modifier" : "configurer"}
          </Button>
        </td>
      </tr>
      {editing && (
        <tr>
          <td colSpan={5}>
            <div className="space-y-2 border border-line bg-surface p-3 text-sm">
              <label className="block space-y-1">
                <span>implémentation</span>
                {types.length > 0 ? (
                  <select
                    value={type}
                    onChange={(event) => setType(event.target.value)}
                    className="w-full rounded border border-line bg-surface px-2 py-1.5"
                  >
                    {types.map((t) => (
                      <option key={t.type} value={t.type} disabled={t.available === false}>
                        {t.display}
                        {t.available === false ? " (indisponible)" : ""}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    value={type}
                    onChange={(event) => setType(event.target.value)}
                    className="w-full rounded border border-line bg-surface px-2 py-1.5 font-mono text-xs"
                  />
                )}
              </label>
              <label className="block space-y-1">
                <span>configuration (JSON — jamais un secret : les secrets sont référencés, pas saisis)</span>
                <textarea
                  value={config}
                  onChange={(event) => setConfig(event.target.value)}
                  rows={5}
                  className="w-full rounded border border-line bg-surface px-2 py-1.5 font-mono text-xs"
                />
              </label>
              {invalide && <ErrorNote>{invalide}</ErrorNote>}
              <div className="flex justify-end gap-2">
                <Button size="sm" onClick={() => setEditing(false)}>
                  annuler
                </Button>
                <Button size="sm" tone="primary" onClick={() => void save()}>
                  enregistrer
                </Button>
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
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
      <YamlEditor label="politique YAML" value={texte} onChange={setTexte} issues={[]} />
      <div className="flex justify-end">
        <Button tone="primary" onClick={() => void onSave(texte)} disabled={!texte || texte === yaml}>
          enregistrer la politique
        </Button>
      </div>
      <p className="text-xs text-ink-muted">
        budgets, approbations, tentatives, périmètre : la même politique que l&apos;orchestrateur applique. Le serveur
        la valide à l&apos;enregistrement.
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
            placeholder={models?.inherited?.[profil] ? `hérité : ${models.inherited[profil]}` : "platform/standard"}
            onChange={(event) => setProfils((p) => ({ ...p, [profil]: event.target.value }))}
            className="w-64 rounded border border-line bg-surface px-2 py-1 font-mono text-xs"
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
        accepter une combinaison backend × modèle non validée par les évals
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
          enregistrer les profils
        </Button>
      </div>
    </div>
  );
}
