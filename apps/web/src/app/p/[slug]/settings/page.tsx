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
  loading: () => <p className="text-sm text-ink-muted">loading the editor…</p>,
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
      setError(cause instanceof Error ? cause.message : "refused");
    }
  }

  async function test(kind: string) {
    await faire(
      async () => {
        const result = await api.testConnector(slug, kind);
        setMessage(
          `${kind}: ${result.ok ? "connection OK" : "failed"} — ` +
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

      <Card title="connectors" className="lg:col-span-2">
        <table>
          <thead>
            <tr>
              <th>kind</th>
              <th>implementation</th>
              <th>status</th>
              <th>last test</th>
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
                    faire(() => api.putConnector(slug, kind, { type, config }), `${kind} connector saved`, ["connectors"])
                  }
                  onTest={connector ? () => test(kind) : undefined}
                />
              );
            })}
          </tbody>
        </table>
      </Card>

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
        )}
        {tools.data?.allows_all && (
          <p className="mt-2 text-xs text-ink-muted">
            this project declares <span className="font-mono">*</span>: the whole catalogue is open to it.
          </p>
        )}
        <p className="mt-2 text-xs text-ink-muted">
          a project&apos;s tool list (`tools`, `groups`) is set in its configuration — it is reviewed like
          code.
        </p>
      </Card>

      <Card title="backend × model matrix" className="lg:col-span-2">
        <p className="mb-2 text-sm text-ink-muted">
          Published by the nightly evals: an unvalidated combination is refused on save.
        </p>
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
                <td className="text-right">{entry.success_rate != null ? `${Math.round(entry.success_rate * 100)} %` : "—"}</td>
                <td className="text-right">{entry.median_cost_usd != null ? `${entry.median_cost_usd.toFixed(2)} $` : "—"}</td>
                <td>{entry.with_memory == null ? "—" : entry.with_memory ? "with" : "without"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {(matrix.data?.entries ?? []).length === 0 && <Empty>no eval published yet</Empty>}
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
      setInvalide("the configuration must be a JSON object");
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
        <td className="font-mono text-xs">{current?.type ?? <span className="text-ink-muted">— not configured</span>}</td>
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
              test
            </Button>
          )}
          <Button size="sm" onClick={() => setEditing((e) => !e)}>
            {current ? "edit" : "configure"}
          </Button>
        </td>
      </tr>
      {editing && (
        <tr>
          <td colSpan={5}>
            <div className="space-y-2 border border-line bg-surface p-3 text-sm">
              <label className="block space-y-1">
                <span>implementation</span>
                {types.length > 0 ? (
                  <select
                    value={type}
                    onChange={(event) => setType(event.target.value)}
                    className="w-full rounded border border-line bg-surface px-2 py-1.5"
                  >
                    {types.map((t) => (
                      <option key={t.type} value={t.type} disabled={t.available === false}>
                        {t.display}
                        {t.available === false ? " (unavailable)" : ""}
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
                <span>configuration (JSON — never a secret: secrets are referenced, not typed)</span>
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
                  cancel
                </Button>
                <Button size="sm" tone="primary" onClick={() => void save()}>
                  save
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
      <YamlEditor label="policy YAML" value={texte} onChange={setTexte} issues={[]} />
      <div className="flex justify-end">
        <Button tone="primary" onClick={() => void onSave(texte)} disabled={!texte || texte === yaml}>
          save the policy
        </Button>
      </div>
      <p className="text-xs text-ink-muted">
        budgets, approvals, attempts, scope: the same policy the orchestrator applies. The server
        validates it on save.
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
