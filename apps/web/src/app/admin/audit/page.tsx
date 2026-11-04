// SPDX-License-Identifier: Apache-2.0
"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Button, Card, Empty, ErrorNote, Input, StateBadge, Table, TBody, TD, TH, THead, TR } from "@/components/ui";
import { api } from "@/lib/api";
import { versCsv } from "@/lib/csv";
import { shortDate } from "@/lib/format";

/**
 * The audit log: filtered by actor and by kind of target, page after page, exportable. The API
 * returns only the organisations where one REALLY has the right to read it (`audit:read`).
 */
export default function AuditPage() {
  const [acteur, setActeur] = useState("");
  const [cible, setCible] = useState("");
  const [filtres, setFiltres] = useState({ actor: "", target_type: "" });
  const pages = useInfiniteQuery({
    queryKey: ["audit", filtres],
    queryFn: ({ pageParam }) => api.audit({ ...filtres, cursor: pageParam, limit: 50 }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => (lastPage.meta.has_more ? (lastPage.meta.next_cursor ?? undefined) : undefined),
  });
  const rows = pages.data?.pages.flatMap((page) => page.items) ?? [];

  function exporter() {
    const csv = versCsv(
      ["when", "actor_kind", "actor", "action", "target_type", "target_id"],
      rows.map((e) => [e.ts, e.actor_kind, e.actor_id, e.action, e.target_type, e.target_id]),
    );
    const lien = document.createElement("a");
    lien.href = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    lien.download = "audit.csv";
    lien.click();
    URL.revokeObjectURL(lien.href);
  }

  return (
    <Card
      title="Audit log"
      action={
        <Button size="sm" onClick={exporter} disabled={rows.length === 0}>
          Export the {rows.length} rows (CSV)
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
          <span className="text-xs text-ink-muted">Actor</span>
          <Input
            value={acteur}
            onChange={(event) => setActeur(event.target.value)}
            placeholder="Actor (e-mail, agent)"
            className="w-auto"
          />
        </label>
        <label className="flex min-w-0 flex-col gap-1">
          <span className="text-xs text-ink-muted">Target type</span>
          <Input
            value={cible}
            onChange={(event) => setCible(event.target.value)}
            placeholder="Target type (work_item, user…)"
            className="w-auto"
          />
        </label>
        <Button size="sm" type="submit">
          Filter
        </Button>
      </form>
      {pages.error && <ErrorNote>{String(pages.error)}</ErrorNote>}
      {rows.length === 0 && !pages.isLoading ? (
        <Empty>No audit entry</Empty>
      ) : (
        <Table aria-label="Audit entries">
          <THead>
            <TR>
              <TH>When</TH>
              <TH>Actor</TH>
              <TH>Action</TH>
              <TH>Target</TH>
            </TR>
          </THead>
          <TBody>
            {rows.map((entry) => (
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
            More
          </Button>
        </div>
      )}
    </Card>
  );
}
