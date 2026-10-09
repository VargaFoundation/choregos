// SPDX-License-Identifier: Apache-2.0
"use client";

import { Heading } from "@varga/design-system";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { use, useState } from "react";
import { DecisionBar } from "@/components/decision-bar";
import { ActorIcon, Button, Card, CostChip, Empty, ErrorNote, StateBadge } from "@/components/ui";
import { columnsFromGraph, itemsOf } from "@/components/workflows/board";
import { champsDepuisSchema, valeursPourLApi, type Champ } from "@/components/workflows/champs";
import { useWorkflow } from "@/components/workflows/use-workflow";
import { api } from "@/lib/api";
import { relative } from "@/lib/format";

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
  // Quand le tracker est interne, la demande se pose ICI — sinon elle vient de GitHub/Jira.
  const trackerInterne = (connectors.data ?? []).some(
    (c) => c.kind === "tracker" && ["internal", "fake"].includes(c.type),
  );
  const [nouvelle, setNouvelle] = useState(false);

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
          board
        </Heading>
        {(workflows.data?.length ?? 0) > 1 ? (
          <label className="flex items-center gap-2 text-sm text-ink-muted">
            workflow
            <select
              aria-label="board workflow"
              value={choisi}
              onChange={(event) => router.replace(`${pathname}?workflow=${encodeURIComponent(event.target.value)}`)}
              className="rounded border border-line bg-surface px-2 py-1 text-ink"
            >
              {workflows.data?.map((w) => (
                <option key={w.name} value={w.name}>
                  {w.name}
                  {w.is_default ? " (default)" : ""}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        <span className="text-sm text-ink-muted">
          {choisi || "—"} v{definition.data?.version ?? "?"}
        </span>
        {trackerInterne && (
          <span className="ml-auto">
            <Button tone="accent" size="sm" onClick={() => setNouvelle((n) => !n)}>
              new request
            </Button>
          </span>
        )}
      </div>
      {nouvelle && (
        <NouvelleDemande
          slug={slug}
          workflows={workflows.data?.map((w) => w.name) ?? []}
          initial={choisi}
          onDone={() => {
            setNouvelle(false);
            void queryClient.invalidateQueries({ queryKey: ["items", slug] });
          }}
        />
      )}
      {items.isLoading && <Empty>loading…</Empty>}
      {enAttente.length > 0 && (
        <div
          className="border border-line border-l-2 border-l-warn bg-surface p-3 text-sm"
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
        <nav aria-label="columns of the board" className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
          {columns.map((column) => {
            const attentes = column.items.filter((item) => item.pending_request).length;
            return (
              <a key={column.state} href={`#colonne-${column.state}`} className="text-ink-muted hover:text-ink">
                {column.display} <span className="tabular-nums">{column.items.length}</span>
                {attentes > 0 && <span className="font-medium text-warn"> · {attentes} waiting</span>}
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
            className="scroll-mt-20 space-y-2"
          >
            <header className="flex items-center justify-between">
              <StateBadge state={column.state} display={column.display} kind={column.kind} />
              <span className="text-xs text-ink-muted">{column.items.length}</span>
            </header>
            {column.horsWorkflow && (
              // Sans ce mot, une colonne « In progress » après « Done » ne se comprenait pas (audit du 08/10).
              <p className="text-xs text-ink-muted">
                not a state of this workflow — tickets left here by an older version
              </p>
            )}
            {column.items.map((item) => (
              <div
                key={item.id}
                id={`carte-${item.id}`}
                className="scroll-mt-20 target:outline target:outline-2 target:outline-accent"
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
                    <p className="mt-1 text-xs font-medium text-danger" title={item.failure.message}>
                      dead · {item.failure.activity ?? "interpreter"}
                    </p>
                  )}
                  {item.pending_request && (
                    <div className="mt-2 space-y-2 border border-line border-l-2 border-l-warn bg-surface p-2">
                      <p className="text-xs text-warn">
                        {String(item.pending_request.payload?.summary ?? item.pending_request.kind)} ·{" "}
                        {relative(item.pending_request.requested_at)}
                      </p>
                      {item.pending_request.kind === "task" ? (
                        // Un formulaire et une attestation ne tiennent pas dans une carte : la page du ticket.
                        <Link href={`/p/${slug}/items/${item.id}`} className="text-xs">
                          do the task
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

/**
 * Poser une demande dans Choregos : son workflow, ses champs (lus dans `metadata.inputs`), un titre,
 * un corps, une taille. L'interpréteur démarre tout de suite ; l'API valide les champs.
 */
function NouvelleDemande({
  slug,
  workflows,
  initial,
  onDone,
}: {
  slug: string;
  workflows: string[];
  initial: string;
  onDone: () => void;
}) {
  const [workflow, setWorkflow] = useState(initial);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [size, setSize] = useState<"S" | "M" | "L" | "XL" | "">("");
  const [saisies, setSaisies] = useState<Record<string, string | boolean>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { definition } = useWorkflow(slug, workflow);
  const champs = champsDepuisSchema(definition.data?.json?.metadata?.inputs as Record<string, unknown> | undefined);

  async function poser() {
    setBusy(true);
    setError(null);
    try {
      await api.createWorkItem(slug, {
        title,
        body,
        size: size || null,
        start: true,
        workflow: workflow || null,
        fields: valeursPourLApi(champs, saisies),
      });
      onDone();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "request refused");
      setBusy(false);
    }
  }

  return (
    <Card className="p-3">
      <form
        className="space-y-2 text-sm"
        onSubmit={(event) => {
          event.preventDefault();
          void poser();
        }}
      >
        {workflows.length > 1 && (
          <select
            aria-label="workflow of the request"
            value={workflow}
            onChange={(event) => {
              setWorkflow(event.target.value);
              setSaisies({});
            }}
            className="rounded border border-line bg-surface px-2 py-1"
          >
            {workflows.map((nom) => (
              <option key={nom} value={nom}>
                {nom}
              </option>
            ))}
          </select>
        )}
        {champs.length > 0 && (
          <fieldset className="grid gap-2 md:grid-cols-2" data-testid="request-fields">
            <legend className="sr-only">fields of the request</legend>
            {champs.map((champ) => (
              <ChampDeDemande
                key={champ.name}
                champ={champ}
                valeur={saisies[champ.name]}
                onChange={(valeur) => setSaisies((avant) => ({ ...avant, [champ.name]: valeur }))}
              />
            ))}
          </fieldset>
        )}
        <input
          aria-label="request title"
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          placeholder="Data project manager for a 6-month assignment"
          className="w-full rounded border border-line bg-surface px-2 py-1.5"
        />
        <textarea
          aria-label="request details"
          value={body}
          onChange={(event) => setBody(event.target.value)}
          rows={4}
          placeholder="Client, start date, expected outcome, constraints…"
          className="w-full rounded border border-line bg-surface px-2 py-1.5"
        />
        <div className="flex items-center gap-2">
          <select
            aria-label="size"
            value={size}
            onChange={(event) => setSize(event.target.value as typeof size)}
            className="rounded border border-line bg-surface px-2 py-1"
          >
            <option value="">size?</option>
            {["S", "M", "L", "XL"].map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <Button tone="primary" size="sm" type="submit" disabled={busy || !title}>
            file and start
          </Button>
        </div>
        {error && <ErrorNote>{error}</ErrorNote>}
      </form>
    </Card>
  );
}

/** Un champ de la demande, au contrôle que son type appelle. */
function ChampDeDemande({
  champ,
  valeur,
  onChange,
}: {
  champ: Champ;
  valeur: string | boolean | undefined;
  onChange: (valeur: string | boolean) => void;
}) {
  const libelle = `${champ.label}${champ.required ? " (required)" : ""}`;
  const classes = "w-full rounded border border-line bg-surface px-2 py-1.5";
  if (champ.control === "boolean") {
    return (
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={Boolean(valeur)} onChange={(event) => onChange(event.target.checked)} />
        {libelle}
      </label>
    );
  }
  if (champ.control === "choice") {
    return (
      <select
        aria-label={libelle}
        value={String(valeur ?? "")}
        onChange={(event) => onChange(event.target.value)}
        className={classes}
      >
        <option value="">{libelle}</option>
        {champ.choices?.map((choix) => (
          <option key={choix} value={choix}>
            {choix}
          </option>
        ))}
      </select>
    );
  }
  const type =
    champ.control === "date" ? "date" : champ.control === "number" || champ.control === "integer" ? "number" : "text";
  return (
    <input
      aria-label={libelle}
      title={champ.hint}
      type={type}
      required={champ.required}
      value={String(valeur ?? "")}
      placeholder={champ.control === "list" ? `${champ.label}, comma separated` : champ.label}
      onChange={(event) => onChange(event.target.value)}
      className={classes}
    />
  );
}
