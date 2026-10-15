// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { use, useState } from "react";
import { DecisionBar } from "@/components/decision-bar";
import { ActorIcon, Button, Card, CostChip, Dot, Drawer, Empty, ErrorNote, Heading, LEGENDE, Select, tonDEtat } from "@/components/ui";
import { columnsFromGraph, itemsOf } from "@/components/workflows/board";
import { NewWorkItemForm } from "@/components/workflows/new-work-item";
import { useWorkflow } from "@/components/workflows/use-workflow";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { relative } from "@/lib/format";
import { trackerOf } from "@/lib/tracker";
import type { WorkItemDto } from "@/lib/types";

/**
 * Un board par workflow (ADR 0031, S16-10) : ses colonnes sont les états du workflow, lus par le
 * validateur ; ses tickets, ceux qui y sont épinglés. `?workflow=` garde le choix dans l'URL.
 */
export default function BoardPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const queryClient = useQueryClient();
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams();
  const items = useQuery({ queryKey: ["items", slug], queryFn: () => api.workItems(slug) });
  const workflows = useQuery({ queryKey: ["workflows", slug], queryFn: () => api.workflows(slug) });
  const defaut = workflows.data?.find((w) => w.is_default)?.name ?? workflows.data?.[0]?.name ?? "";
  const choisi = search.get("workflow") ?? defaut;
  const { definition, validation } = useWorkflow(slug, choisi);
  const connectors = useQuery({ queryKey: ["connectors", slug], queryFn: () => api.connectors(slug) });
  // A work item is filed from here whatever the tracker: with an external one the platform creates
  // the issue there first (ADR 0059).
  const tracker = trackerOf(connectors.data);
  const [nouvelle, setNouvelle] = useState(false);
  const [created, setCreated] = useState<WorkItemDto | null>(null);

  if (items.error) return <ErrorNote>{(items.error as Error).message}</ErrorNote>;

  const columns = columnsFromGraph(validation.data?.graph, itemsOf(items.data?.items ?? [], choisi, choisi === defaut));
  // Ce qui attend une personne, et où : sur grand écran les colonnes du bout sortent du champ, et la
  // seule carte avec un bouton « approve » s'y trouvait sans indice (seconde passe du 08/10, S23-09).
  const enAttente = columns.flatMap((column) =>
    column.items.filter((item) => item.pending_request).map((item) => ({ item, column })),
  );

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <Heading as="h2" size="md">
          Board
        </Heading>
        {(workflows.data?.length ?? 0) > 1 ? (
          <label className="flex items-center gap-2 text-sm text-ink-muted">
            Workflow
            <Select
              aria-label="Board workflow"
              value={choisi}
              onChange={(event) => router.replace(`${pathname}?workflow=${encodeURIComponent(event.target.value)}`)}
              className="w-auto"
            >
              {workflows.data?.map((w) => (
                <option key={w.name} value={w.name}>
                  {w.name}
                  {w.is_default ? " (default)" : ""}
                </option>
              ))}
            </Select>
          </label>
        ) : null}
        <span className="text-sm text-ink-muted">
          {choisi || "—"} v{definition.data?.version ?? "?"}
        </span>
        <span className="ml-auto">
          <Button tone="accent" size="sm" onClick={() => setNouvelle(true)}>
            New work item
          </Button>
        </span>
      </div>
      <Drawer open={nouvelle} onClose={() => setNouvelle(false)} title="New work item">
        {nouvelle && (
          <NewWorkItemForm
            slug={slug}
            workflows={workflows.data?.map((w) => w.name) ?? []}
            initial={choisi}
            tracker={tracker}
            onDone={(item) => {
              setNouvelle(false);
              setCreated(item);
              void queryClient.invalidateQueries({ queryKey: ["items", slug] });
            }}
          />
        )}
      </Drawer>
      {created && (
        <p role="status" className="text-sm" data-testid="work-item-created">
          Created <span className="font-mono">{created.tracker_key}</span>
          {" · "}
          <Link href={`/p/${slug}/items/${created.id}`}>open it here</Link>
          {created.url && (
            <>
              {" · "}
              <a href={created.url} target="_blank" rel="noreferrer">
                open the issue
              </a>
            </>
          )}
        </p>
      )}
      {items.isLoading && <Empty>Loading…</Empty>}
      {enAttente.length > 0 && (
        <div
          className="raised border border-line border-l-2 border-l-waiting p-3 text-sm"
          data-testid="attentes-du-board"
        >
          <p className="font-medium">
            {enAttente.length} ticket{enAttente.length > 1 ? "s" : ""} wait{enAttente.length > 1 ? "" : "s"} for a
            person
          </p>
          <ul className="mt-1 space-y-1">
            {enAttente.map(({ item, column }) => (
              <li key={item.id}>
                <a href={`#carte-${item.id}`}>{item.title}</a>
                <span className="text-ink-muted"> · in {column.display}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {columns.length > 0 && (
        // Le sommaire des colonnes : chacune y est, même hors champ, avec ce qu'elle tient.
        <nav aria-label="Columns of the board" data-donnee className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
          {columns.map((column) => {
            const attentes = column.items.filter((item) => item.pending_request).length;
            return (
              <a key={column.state} href={`#colonne-${column.state}`} className="text-ink-muted hover:text-ink">
                {column.display} <span className="tabular-nums">{column.items.length}</span>
                {attentes > 0 && <span className="font-medium text-waiting-ink"> · {attentes} waiting</span>}
              </a>
            );
          })}
        </nav>
      )}
      <div className="grid gap-3 overflow-x-auto pb-2 md:grid-flow-col md:auto-cols-[minmax(260px,1fr)]">
        {columns.map((column) => (
          <section
            key={column.state}
            id={`colonne-${column.state}`}
            aria-label={`${column.display}, ${column.items.length} ticket${column.items.length > 1 ? "s" : ""}`}
            className="scroll-mt-16 space-y-2"
          >
            <header className="flex h-8 items-center gap-2 border-b border-line" title={column.state}>
              <Dot tone={tonDEtat(column.state, column.kind)} />
              <h3 className={cn(LEGENDE, "truncate")}>{column.display}</h3>
              <span className="ml-auto rounded-full bg-surface-muted px-1.5 font-mono text-[11px] tabular-nums text-ink-muted">
                {column.items.length}
              </span>
            </header>
            {column.horsWorkflow && (
              // Sans ce mot, une colonne « In progress » après « Done » ne se comprenait pas (audit du 08/10).
              <p className="text-xs text-ink-muted">
                Not a state of this workflow — tickets left here by an older version
              </p>
            )}
            {column.items.map((item) => (
              <div
                key={item.id}
                id={`carte-${item.id}`}
                className="scroll-mt-16 target:outline target:outline-2 target:outline-accent"
              >
                <Card className="p-3">
                  <div className="flex items-start justify-between gap-2">
                    <Link href={`/p/${slug}/items/${item.id}`} className="text-sm font-medium no-underline">
                      {item.title}
                    </Link>
                    <CostChip costUsd={item.totals?.cost_usd} tokensIn={item.totals?.tokens_in} />
                  </div>
                  <p className="mt-1 font-mono text-xs text-ink-muted">{item.tracker_key}</p>
                  {item.current_run && (
                    <p className="mt-2 flex items-center gap-2 text-xs">
                      <ActorIcon
                        kind="agent"
                        name={`${item.current_run.stage_role} · attempt ${item.current_run.attempt}`}
                      />
                      <span className="text-ink-muted">{item.current_run.status}</span>
                    </p>
                  )}
                  {item.failure && (
                    <p className="mt-1 text-xs font-medium text-failed-ink" title={item.failure.message}>
                      Dead · {item.failure.activity ?? "interpreter"}
                    </p>
                  )}
                  {item.pending_request && (
                    <div className="mt-2 space-y-2 border border-line border-l-2 border-l-waiting bg-surface p-2">
                      <p className="text-xs text-waiting-ink">
                        {String(item.pending_request.payload?.summary ?? item.pending_request.kind)} ·{" "}
                        {relative(item.pending_request.requested_at)}
                      </p>
                      {item.pending_request.kind === "task" ? (
                        // Un formulaire et une attestation ne tiennent pas dans une carte : la page du ticket.
                        <Link href={`/p/${slug}/items/${item.id}`} className="text-xs">
                          Do the task
                        </Link>
                      ) : (
                        <DecisionBar
                          itemId={item.id}
                          kind={item.pending_request.kind}
                          onDone={() => queryClient.invalidateQueries({ queryKey: ["items", slug] })}
                        />
                      )}
                    </div>
                  )}
                </Card>
              </div>
            ))}
          </section>
        ))}
      </div>
    </div>
  );
}
