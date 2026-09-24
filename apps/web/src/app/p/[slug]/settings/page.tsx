"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { use, useState } from "react";
import { Button, Card, Empty, ErrorNote, StateBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { eur, shortDate } from "@/lib/format";

export default function SettingsPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const queryClient = useQueryClient();
  const connectors = useQuery({ queryKey: ["connectors", slug], queryFn: () => api.connectors(slug) });
  const policy = useQuery({ queryKey: ["policy", slug], queryFn: () => api.policy(slug) });
  const matrix = useQuery({ queryKey: ["matrix", slug], queryFn: () => api.modelMatrix(slug) });
  const tools = useQuery({ queryKey: ["project-tools", slug], queryFn: () => api.projectTools(slug) });
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function test(kind: string) {
    setError(null);
    setMessage(null);
    try {
      const result = await api.testConnector(slug, kind);
      setMessage(
        `${kind} : ${result.ok ? "connexion OK" : "échec"} — ` +
          result.checks.map((check) => `${check.name} ${check.ok ? "✓" : "✗"}`).join(", "),
      );
      queryClient.invalidateQueries({ queryKey: ["connectors", slug] });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "test impossible");
    }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="connecteurs">
        {message && <p className="mb-2 text-sm text-ok">{message}</p>}
        {error && <ErrorNote>{error}</ErrorNote>}
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
            {(connectors.data ?? []).map((connector) => (
              <tr key={connector.kind}>
                <td>{connector.kind}</td>
                <td className="font-mono text-xs">{connector.type}</td>
                <td>
                  <StateBadge
                    state={connector.status}
                    display={connector.status}
                    kind={connector.status === "ok" ? "terminal" : connector.status === "error" ? "blocked" : "wait"}
                  />
                </td>
                <td className="text-xs text-ink-muted">{shortDate(connector.last_check_at)}</td>
                <td>
                  <Button onClick={() => test(connector.kind)}>tester</Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {connectors.data?.length === 0 && <Empty>aucun connecteur configuré</Empty>}
      </Card>

      <Card title="outils du catalogue">
        {/* Les deux ensembles, pas seulement ce qui est autorisé : sans la liste complète,
            on ne peut ni savoir ce qu'on pourrait ouvrir, ni comprendre pourquoi un outil
            manque à l'agent. La clé d'un fournisseur n'apparaît jamais — seulement le fait
            qu'il en faille une. */}
        {(tools.data?.tools ?? []).length === 0 ? (
          <Empty>aucun outil déclaré par le déploiement.</Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <th>outil</th>
                <th>fournisseur</th>
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
                    {tool.needs_credential ? " · clé plateforme" : " · sans clé"}
                  </td>
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
      </Card>

      <Card title="politique">
        <pre className="max-h-96 overflow-auto rounded border border-line bg-surface-muted p-3 font-mono text-xs">
          {policy.data?.yaml ?? "—"}
        </pre>
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
