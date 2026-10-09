// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { DecisionBar } from "@/components/decision-bar";
import { verdictsDe } from "@/components/garanties";
import { LiveLog } from "@/components/live-log";
import { Empty, PhraseDuMoteur } from "@/components/ui";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { duration, relative, shortDate, usd } from "@/lib/format";
import { useEventStream } from "@/lib/sse";
import type { JourneyStep, ProcessStep, RunEventDto } from "@/lib/types";
import type { Etape, EtatEtape } from "./modele";

const GENRE: Record<Etape["genre"], string> = { agent: "agent", human: "person", platform: "platform", train: "release train" };
const EN_COURS = new Set(["queued", "running", "waiting", "pending_approval"]);

/**
 * Le panneau d'une étape du parcours (S22-02) : ce qu'elle fait, puis chacune de ses tentatives. Pour
 * un agent, ce qu'il a rendu, ses preuves, ses garanties, ce à quoi il a touché, son diff et son
 * journal — en direct quand il tourne. Pour une personne, la demande, qui a décidé et pourquoi ; et,
 * si on l'attend encore, de quoi décider ici même.
 */
export function PanneauDEtape({
  etape,
  etat,
  process,
  slug,
  itemId,
  onClose,
  onDecided,
}: {
  etape: Etape;
  etat: EtatEtape;
  process?: ProcessStep;
  slug: string;
  itemId: string;
  onClose: () => void;
  onDecided?: () => void;
}) {
  const tentatives = etat.tentatives;
  const [choisie, setChoisie] = useState<string | null>(null);
  const pas = tentatives.find((t) => t.id === choisie) ?? tentatives.at(-1);
  return (
    <aside
      className="min-w-0 self-start rounded border border-line bg-surface p-4 xl:sticky xl:top-4"
      aria-label={`${etape.libelle}: attempts, actions and log`}
      data-testid="panneau-parcours"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs uppercase tracking-wide text-ink-muted">
            {GENRE[etape.genre]}
            {etape.acteur && etape.genre !== "platform" ? ` · ${etape.acteur}` : ""}
          </p>
          <h3 className="text-lg font-semibold text-ink">
            {etape.libelle} <span className="font-normal text-ink-muted">{etape.action}</span>
          </h3>
          <p className="font-mono text-xs text-ink-muted">transition {etape.id}</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded px-2 py-1 text-sm text-ink-muted hover:text-ink"
          aria-label="close the panel"
        >
          ✕
        </button>
      </div>

      {(etape.phrase || (process?.gates ?? []).length > 0) && (
        <details className="mt-3 text-sm">
          <summary className="cursor-pointer text-ink-muted">what this step does</summary>
          {etape.phrase && (
            <p className="mt-1 text-ink-muted">
              <PhraseDuMoteur texte={etape.phrase} />
            </p>
          )}
          {(process?.gates ?? []).length > 0 && (
            <ul className="mt-1 space-y-0.5 text-xs text-ink-muted">
              {process!.gates!.map((g) => (
                <li key={g.name}>
                  <span className="font-mono">{g.name}</span> — {g.summary}
                </li>
              ))}
            </ul>
          )}
        </details>
      )}

      {tentatives.length === 0 ? (
        <p className="mt-4 text-sm text-ink-muted">
          {etat.statut === "en_cours"
            ? "The platform is on it: this step has no agent run of its own."
            : etat.statut === "fait"
              ? "Done by the platform, without an agent run."
              : "Not reached yet."}
        </p>
      ) : (
        <>
          <div className="mt-4 flex flex-wrap gap-1.5" role="tablist" aria-label="attempts">
            {tentatives.map((t) => (
              <button
                key={t.id}
                type="button"
                role="tab"
                aria-selected={t.id === pas?.id}
                data-testid={`tentative-${t.id}`}
                onClick={() => setChoisie(t.id)}
                className={cn(
                  "rounded border px-2 py-0.5 text-xs",
                  t.id === pas?.id ? "border-ink text-ink" : "border-line-strong text-ink-muted hover:text-ink",
                )}
              >
                {t.attempt} · {t.status.replace(/_/g, " ")}
              </button>
            ))}
          </div>
          {pas && (
            <div className="mt-3" role="tabpanel">
              {pas.kind === "agent" ? (
                <PasDAgent pas={pas} slug={slug} />
              ) : pas.kind === "human" ? (
                <PasHumain pas={pas} itemId={itemId} onDecided={onDecided} />
              ) : (
                <PasDAction pas={pas} slug={slug} />
              )}
            </div>
          )}
        </>
      )}
    </aside>
  );
}

function Faits({ pas }: { pas: JourneyStep }) {
  const duree = pas.started_at && pas.ended_at ? (Date.parse(pas.ended_at) - Date.parse(pas.started_at)) / 1000 : null;
  return (
    <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-xs">
      <dt className="text-ink-muted">status</dt>
      <dd className={cn(pas.status === "failed" || pas.status === "rejected" ? "text-danger" : "text-ink")}>
        {pas.status.replace(/_/g, " ")}
      </dd>
      <dt className="text-ink-muted">started</dt>
      <dd title={shortDate(pas.started_at)}>{relative(pas.started_at)}</dd>
      {pas.ended_at && (
        <>
          <dt className="text-ink-muted">took</dt>
          <dd>{duration(duree)}</dd>
        </>
      )}
      {pas.model && (
        <>
          <dt className="text-ink-muted">model</dt>
          <dd className="font-mono">{pas.model}</dd>
        </>
      )}
      {pas.kind === "agent" && (
        <>
          <dt className="text-ink-muted">cost</dt>
          <dd>{usd(pas.cost_usd)}</dd>
        </>
      )}
      {pas.decided_by && (
        <>
          <dt className="text-ink-muted">decided by</dt>
          <dd>{pas.decided_by}</dd>
        </>
      )}
      {pas.due_at && !pas.ended_at && (
        <>
          <dt className="text-ink-muted">due</dt>
          <dd title={shortDate(pas.due_at)}>{relative(pas.due_at)}</dd>
        </>
      )}
    </dl>
  );
}

function Preuves({ evidence }: { evidence: Record<string, unknown> }) {
  const faits = Object.entries((evidence.facts ?? {}) as Record<string, unknown>);
  const lignes: [string, string][] = faits.length
    ? faits.map(([k, v]) => [k.replace(/_/g, " "), typeof v === "boolean" ? (v ? "✓" : "✗") : String(v)])
    : [
        ...(typeof evidence.tests_run === "number"
          ? [
              [
                "tests",
                `${Number(evidence.tests_failed ?? 0) > 0 ? "✗" : "✓"} ${evidence.tests_run} run, ${evidence.tests_failed ?? 0} failed`,
              ] as [string, string],
            ]
          : []),
        ...(["lint", "typecheck", "security_scan"] as const)
          .filter((k) => evidence[k])
          .map((k) => [k.replace(/_/g, " "), String(evidence[k])] as [string, string]),
        ...(typeof evidence.coverage_delta === "number"
          ? [["coverage", `${evidence.coverage_delta > 0 ? "+" : ""}${evidence.coverage_delta}`] as [string, string]]
          : []),
      ];
  if (lignes.length === 0) return null;
  return (
    <ul className="flex flex-wrap gap-1.5 text-xs" aria-label="evidence">
      {lignes.map(([nom, valeur]) => (
        <li key={nom} className="rounded border border-line px-1.5 py-0.5">
          <span className="text-ink-muted">{nom}</span> {valeur}
        </li>
      ))}
    </ul>
  );
}

function PasDAgent({ pas, slug }: { pas: JourneyStep; slug: string }) {
  const [vue, setVue] = useState<"actions" | "journal">("actions");
  const tourne = EN_COURS.has(pas.status) && !pas.ended_at;
  const stockes = useQuery({ queryKey: ["run-events", pas.id], queryFn: () => api.runEvents(pas.id) });
  const direct = useEventStream<RunEventDto>({
    path: `/runs/${pas.id}/events`,
    enabled: tourne,
    mockEvents: () => import("@/mocks/data").then((fixtures) => fixtures.runEvents),
  });
  const evenements = dedoublonner([...(stockes.data ?? []), ...direct.events]);
  return (
    <div className="space-y-3">
      <Faits pas={pas} />
      {pas.summary && <p className="text-sm text-ink">{pas.summary}</p>}
      {pas.verdict && (
        <p className={cn("text-sm font-medium", pas.verdict === "approve" ? "text-ok" : "text-warn")}>
          verdict: {pas.verdict.replace(/_/g, " ")}
        </p>
      )}
      <Preuves evidence={(pas.evidence ?? {}) as Record<string, unknown>} />
      <div className="flex gap-4 border-b border-line text-sm" role="tablist" aria-label="what the agent did">
        {(
          [
            ["actions", "What it did"],
            ["journal", tourne ? "Log · live" : "Log"],
          ] as const
        ).map(([cle, libelle]) => (
          <button
            key={cle}
            type="button"
            role="tab"
            aria-selected={vue === cle}
            onClick={() => setVue(cle)}
            className={cn(
              "-mb-px border-b-2 py-1.5",
              vue === cle ? "border-ink text-ink" : "border-transparent text-ink-muted hover:text-ink",
            )}
          >
            {libelle}
          </button>
        ))}
      </div>
      {vue === "actions" ? <CeQuIlAFait runId={pas.id} evenements={evenements} /> : <LiveLog events={evenements} height={320} />}
      <Link href={`/p/${slug}/runs/${pas.id}`} className="inline-block text-sm">
        open the full run →
      </Link>
    </div>
  );
}

/** Ce que l'agent a fait : ses garanties, ce à quoi il a touché (les refus d'abord), les fichiers qu'il a changés. */
function CeQuIlAFait({ runId, evenements }: { runId: string; evenements: RunEventDto[] }) {
  const acces = useQuery({ queryKey: ["run-access", runId], queryFn: () => api.runAccess(runId) });
  const diff = useQuery({ queryKey: ["run-diff", runId], queryFn: () => api.runDiff(runId) });
  const garanties = verdictsDe(evenements);
  const lignes = acces.data?.acces ?? [];
  const fichiers = diff.data?.files ?? [];
  return (
    <div className="space-y-3 text-sm" data-testid="ce-qu-il-a-fait">
      <section>
        <h4 className="text-xs uppercase tracking-wide text-ink-muted">gates</h4>
        {garanties.length === 0 ? (
          <p className="text-xs text-ink-muted">no gate evaluated on this run.</p>
        ) : (
          <ul className="space-y-0.5">
            {garanties.map((g, i) => (
              <li key={`${g.name}-${i}`} className="flex gap-2 text-xs">
                <span
                  className={g.pending ? "text-warn" : g.passed ? "text-ok" : "text-danger"}
                  aria-label={g.pending ? "pending" : g.passed ? "passed" : "refused"}
                >
                  {g.pending ? "…" : g.passed ? "✓" : "✗"}
                </span>
                <span>
                  <span className="font-mono">{g.name}</span>
                  {g.detail && <span className="text-ink-muted"> — {g.detail}</span>}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section>
        <h4 className="text-xs uppercase tracking-wide text-ink-muted">
          access {acces.data ? `· ${acces.data.evenements} events, ${acces.data.refus} refused` : ""}
        </h4>
        {lignes.length === 0 ? (
          <p className="text-xs text-ink-muted">no access recorded.</p>
        ) : (
          <ul className="space-y-0.5 text-xs">
            {[...lignes]
              .sort((a, b) => b.refus - a.refus)
              .map((l) => (
                <li key={`${l.nature}:${l.cible}`} className={cn("flex gap-2", l.refus > 0 && "text-danger")}>
                  <span className="w-14 shrink-0 text-ink-muted">{l.nature}</span>
                  <span className="min-w-0 flex-1">
                    <span className="block break-all font-mono">{l.cible}</span>
                    {l.refus > 0 && <span className="block">refused{l.motifs?.length ? `: ${l.motifs.join(" · ")}` : ""}</span>}
                  </span>
                </li>
              ))}
          </ul>
        )}
      </section>
      <section>
        <h4 className="text-xs uppercase tracking-wide text-ink-muted">
          files changed {diff.data ? `· +${diff.data.additions} −${diff.data.deletions}` : ""}
        </h4>
        {fichiers.length === 0 ? (
          <Empty>no file changed</Empty>
        ) : (
          <ul className="space-y-0.5 text-xs">
            {fichiers.map((f) => (
              <li key={f.path} className="flex gap-2">
                <span className="min-w-0 flex-1 break-all font-mono">{f.path}</span>
                <span className="text-ok">+{f.additions}</span>
                <span className="text-danger">−{f.deletions}</span>
                {f.in_scope === false && <span className="text-danger">out of scope</span>}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function PasHumain({ pas, itemId, onDecided }: { pas: JourneyStep; itemId: string; onDecided?: () => void }) {
  const attendu = pas.status === "waiting";
  return (
    <div className="space-y-3">
      <Faits pas={pas} />
      {pas.summary && <p className="text-sm text-ink">{pas.summary}</p>}
      {attendu ? (
        <div className="rounded border border-line p-3">
          <p className="mb-2 text-xs uppercase tracking-wide text-ink-muted">decide here</p>
          <DecisionBar itemId={itemId} kind={pas.role ?? "approval"} onDone={onDecided} />
        </div>
      ) : (
        <p className={cn("text-sm", pas.status === "rejected" ? "text-warn" : "text-ok")}>
          {pas.status.replace(/_/g, " ")}
          {pas.decided_by ? ` by ${pas.decided_by}` : ""}
          {pas.ended_at ? `, ${relative(pas.ended_at)}` : ""}
        </p>
      )}
    </div>
  );
}

function PasDAction({ pas, slug }: { pas: JourneyStep; slug: string }) {
  const train = pas.actor === "release_train";
  return (
    <div className="space-y-3">
      <Faits pas={pas} />
      {pas.summary && <p className="text-sm text-ink">{pas.summary}</p>}
      <Link href={train ? `/p/${slug}/trains` : `/p/${slug}/actions/${pas.id}`} className="inline-block text-sm">
        {train ? "open the release trains →" : "open the governed action →"}
      </Link>
    </div>
  );
}

function dedoublonner(evenements: RunEventDto[]): RunEventDto[] {
  const vus = new Map<number, RunEventDto>();
  for (const e of evenements) vus.set(e.seq, e);
  return [...vus.values()].sort((a, b) => a.seq - b.seq);
}
