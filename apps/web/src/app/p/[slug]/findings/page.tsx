"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { use, useState } from "react";
import { Button, Card, Empty, ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";
import { relative } from "@/lib/format";

const SEVERITY_TONE: Record<string, string> = {
  critical: "text-danger font-semibold",
  high: "text-danger",
  medium: "text-warn",
  low: "text-ink-muted",
};

export default function FindingsPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<string>("");
  const findings = useQuery({
    queryKey: ["findings", slug, status],
    queryFn: () => api.findings(slug, status ? { status } : undefined),
  });
  const [error, setError] = useState<string | null>(null);

  async function act(id: string, action: string) {
    setError(null);
    try {
      await api.findingAction(id, action);
      queryClient.invalidateQueries({ queryKey: ["findings", slug] });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "action refusée");
    }
  }

  return (
    <Card
      title="Findings"
      action={
        <select
          aria-label="Filtrer par état"
          value={status}
          onChange={(event) => setStatus(event.target.value)}
          className="rounded border border-line bg-surface px-2 py-1 text-sm"
        >
          <option value="">tous</option>
          <option value="pending">à trier</option>
          <option value="created">ticket créé</option>
          <option value="duplicate">doublons</option>
          <option value="dismissed">écartés</option>
        </select>
      }
    >
      {error && <ErrorNote>{error}</ErrorNote>}
      <table>
        <thead>
          <tr>
            <th>Sévérité</th>
            <th>Type</th>
            <th>Titre</th>
            <th>Preuve</th>
            <th>Origine</th>
            <th>État</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {(findings.data?.items ?? []).map((finding) => (
            <tr key={finding.id}>
              <td className={SEVERITY_TONE[finding.severity] ?? ""}>{finding.severity}</td>
              <td>{finding.type}</td>
              <td>
                {finding.title}
                {(finding.occurrences ?? 1) > 1 && (
                  <span className="ml-2 rounded bg-surface-muted px-1 text-xs">×{finding.occurrences}</span>
                )}
              </td>
              <td className="max-w-72 truncate font-mono text-xs text-ink-muted" title={finding.evidence}>
                {finding.evidence}
              </td>
              <td className="font-mono text-xs">{finding.origin_work_item_key ?? "—"}</td>
              <td>
                {finding.status}
                {finding.created_work_item_key && (
                  <span className="ml-1 font-mono text-xs text-ink-muted">→ {finding.created_work_item_key}</span>
                )}
                <span className="ml-2 text-xs text-ink-muted">{relative(finding.created_at)}</span>
              </td>
              <td className="space-x-1 whitespace-nowrap">
                {finding.status === "pending" && (
                  <Button tone="primary" onClick={() => act(finding.id, "create_ticket")}>
                    Créer le ticket
                  </Button>
                )}
                {finding.status === "created" && (
                  <Button tone="primary" onClick={() => act(finding.id, "agent_ready")}>
                    Rendre agent-ready
                  </Button>
                )}
                <Button onClick={() => act(finding.id, "mark_duplicate")}>Doublon</Button>
                <Button tone="danger" onClick={() => act(finding.id, "dismiss")}>
                  Ignorer
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {findings.data?.items.length === 0 && <Empty>aucun finding</Empty>}
    </Card>
  );
}
