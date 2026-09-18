"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { use, useState } from "react";
import { Button, Card, Empty, ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";
import { shortDate } from "@/lib/format";

export default function MemoryPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const queryClient = useQueryClient();
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const results = useQuery({
    queryKey: ["memory", slug, submitted],
    queryFn: () => api.memorySearch(slug, submitted),
    enabled: submitted.length > 0,
  });
  const pending = useQuery({ queryKey: ["memory-pending", slug], queryFn: () => api.memoryPending(slug) });
  const [error, setError] = useState<string | null>(null);

  async function decide(id: string, action: "accept" | "reject") {
    setError(null);
    try {
      await api.memoryDecide(slug, id, action);
      queryClient.invalidateQueries({ queryKey: ["memory-pending", slug] });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "décision refusée");
    }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Recherche">
        <form
          className="mb-3 flex gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            setSubmitted(query);
          }}
        >
          <input
            aria-label="Rechercher dans la mémoire"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="arrondis, incident, convention de test…"
            className="flex-1 rounded border border-line bg-surface px-2 py-1.5 text-sm"
          />
          <Button type="submit" tone="primary">
            Chercher
          </Button>
        </form>
        <ul className="space-y-3">
          {(results.data ?? []).map((memory) => (
            <li key={memory.id} className="rounded border border-line p-3">
              <p className="text-xs uppercase tracking-wide text-ink-muted">{memory.kind}</p>
              <p className="font-mono text-xs">{memory.subject}</p>
              <p className="mt-1 text-sm">{memory.content}</p>
              <p className="mt-1 text-xs text-ink-muted">
                valable depuis {shortDate(memory.valid_from)}
                {memory.valid_to ? ` jusqu'au ${shortDate(memory.valid_to)}` : " (en vigueur)"} · source{" "}
                {String((memory.provenance as Record<string, unknown>)?.source ?? "—")}
              </p>
            </li>
          ))}
        </ul>
        {submitted && results.data?.length === 0 && <Empty>aucun souvenir pour « {submitted} »</Empty>}
      </Card>

      <Card title="Faits proposés par des agents">
        {error && <ErrorNote>{error}</ErrorNote>}
        <ul className="space-y-3">
          {(pending.data ?? []).filter((memory) => memory.id).map((memory) => (
            <li key={memory.id} className="rounded border border-warn/30 bg-warn/5 p-3">
              <p className="font-mono text-xs">{memory.subject}</p>
              <p className="mt-1 text-sm">{memory.content}</p>
              <p className="mt-1 text-xs text-ink-muted">proposé par le run {memory.proposed_by ?? "—"}</p>
              <div className="mt-2 flex gap-2">
                <Button tone="primary" onClick={() => decide(memory.id ?? "", "accept")}>
                  Accepter
                </Button>
                <Button tone="danger" onClick={() => decide(memory.id ?? "", "reject")}>
                  Rejeter
                </Button>
              </div>
            </li>
          ))}
        </ul>
        {pending.data?.length === 0 && <Empty>aucun fait en attente</Empty>}
      </Card>
    </div>
  );
}
