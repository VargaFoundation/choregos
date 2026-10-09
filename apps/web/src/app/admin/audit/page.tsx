// SPDX-License-Identifier: Apache-2.0
"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { useState } from "react";
import { TBody, TD, TH, THead, TR, Table } from "@varga/design-system";
import { Button, Card, Empty, ErrorNote, StateBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { versCsv } from "@/lib/csv";
import { shortDate } from "@/lib/format";

/**
 * Le journal d'audit : filtré par acteur et par sorte de cible, page après page, exportable. L'API
 * ne rend que les organisations où l'on a VRAIMENT le droit de le lire (`audit:read`).
 */
export default function AuditPage() {
  const [acteur, setActeur] = useState("");
  const [cible, setCible] = useState("");
  const [filtres, setFiltres] = useState({ actor: "", target_type: "" });
  const pages = useInfiniteQuery({
    queryKey: ["audit", filtres],
    queryFn: ({ pageParam }) => api.audit({ ...filtres, cursor: pageParam, limit: 50 }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (derniere) => (derniere.meta.has_more ? (derniere.meta.next_cursor ?? undefined) : undefined),
  });
  const lignes = pages.data?.pages.flatMap((page) => page.items) ?? [];

  function exporter() {
    const csv = versCsv(
      ["when", "actor_kind", "actor", "action", "target_type", "target_id"],
      lignes.map((e) => [e.ts, e.actor_kind, e.actor_id, e.action, e.target_type, e.target_id]),
    );
    const lien = document.createElement("a");
    lien.href = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    lien.download = "audit.csv";
    lien.click();
    URL.revokeObjectURL(lien.href);
  }

  return (
    <Card
      title="audit log"
      action={
        <Button size="sm" onClick={exporter} disabled={lignes.length === 0}>
          export the {lignes.length} rows (CSV)
        </Button>
      }
    >
      <form
        className="mb-3 flex flex-wrap items-end gap-2 text-sm"
        onSubmit={(event) => {
          event.preventDefault();
          setFiltres({ actor: acteur.trim(), target_type: cible.trim() });
        }}
      >
        <label className="flex min-w-0 flex-col gap-1">
          <span className="text-xs text-ink-muted">actor</span>
          <input
            value={acteur}
            onChange={(event) => setActeur(event.target.value)}
            placeholder="actor (e-mail, agent)"
            className="rounded border border-line bg-surface px-2 py-1"
          />
        </label>
        <label className="flex min-w-0 flex-col gap-1">
          <span className="text-xs text-ink-muted">target type</span>
          <input
            value={cible}
            onChange={(event) => setCible(event.target.value)}
            placeholder="target type (work_item, user…)"
            className="rounded border border-line bg-surface px-2 py-1"
          />
        </label>
        <Button size="sm" type="submit">
          filter
        </Button>
      </form>
      {pages.error && <ErrorNote>{String(pages.error)}</ErrorNote>}
      {lignes.length === 0 && !pages.isLoading ? (
        <Empty>no audit entry</Empty>
      ) : (
        <Table aria-label="audit entries">
          <THead>
            <TR>
              <TH>when</TH>
              <TH>actor</TH>
              <TH>action</TH>
              <TH>target</TH>
            </TR>
          </THead>
          <TBody>
            {lignes.map((entry) => (
              <TR key={entry.id}>
                <TD>{shortDate(entry.ts)}</TD>
                <TD>
                  <StateBadge state={entry.actor_kind} display={entry.actor_kind} kind="work" /> {entry.actor_id ?? "—"}
                </TD>
                <TD>
                  <code className="text-xs">{entry.action}</code>
                </TD>
                <TD>
                  {entry.target_type}
                  {entry.target_id ? ` ${entry.target_id.slice(0, 8)}` : ""}
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      )}
      {pages.hasNextPage && (
        <div className="mt-3">
          <Button size="sm" onClick={() => void pages.fetchNextPage()} disabled={pages.isFetchingNextPage}>
            more
          </Button>
        </div>
      )}
    </Card>
  );
}
