// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { DecisionBar } from "@/components/decision-bar";
import { verdictsDe } from "@/components/garanties";
import { LiveLog } from "@/components/live-log";
import { PlanDeLAgent } from "@/components/plan-de-l-agent";
import { Provenance } from "@/components/provenance";
import { Empty, LEGENDE, PhraseDuMoteur } from "@/components/ui";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { duration, relative, shortDate, usd } from "@/lib/format";
import { planDeLAgent } from "@/lib/plan-de-l-agent";
import { provenanceDePreuve } from "@/lib/provenance";
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
      className="raised min-w-0 self-start border border-line p-4 xl:sticky xl:top-16"
      aria-label={`${etape.libelle}: attempts, actions and log`}
      data-testid="panneau-parcours"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className={LEGENDE}>
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
          className="px-2 py-1 text-sm text-ink-muted hover:text-ink pointer-coarse:min-h-11"
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
                  "border px-2 py-0.5 font-mono text-xs pointer-coarse:min-h-11",
                  t.id === pas?.id
                    ? "border-accent-line bg-accent-soft text-accent-strong"
                    : "border-line text-ink-muted hover:text-ink",
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
      <dd className={cn(pas.status === "failed" || pas.status === "rejected" ? "text-failed-ink" : "text-ink")}>
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
  // [libellé, valeur, champ du contrat] : le champ dit, par `measured`, qui l'a établi (ADR 0045).
  const lignes: [string, string, string][] = faits.length
    ? faits.map(([k, v]) => [k.replace(/_/g, " "), typeof v === "boolean" ? (v ? "✓" : "✗") : String(v), `facts.${k}`])
    : [
        ...(typeof evidence.tests_run === "number"
          ? [
              [
                "tests",
                `${Number(evidence.tests_failed ?? 0) > 0 ? "✗" : "✓"} ${evidence.tests_run} run, ${evidence.tests_failed ?? 0} failed`,
                "tests_run",
              ] as [string, string, string],
            ]
          : []),
        ...(["lint", "typecheck", "security_scan"] as const)
          .filter((k) => evidence[k])
          .map((k) => [k.replace(/_/g, " "), String(evidence[k]), k] as [string, string, string]),
        ...(typeof evidence.coverage_delta === "number"
          ? [
              [
                "coverage",
                `${evidence.coverage_delta > 0 ? "+" : ""}${evidence.coverage_delta}`,
                "coverage_delta",
              ] as [string, string, string],
            ]
          : []),
      ];
  if (lignes.length === 0) return null;
  const mesures = Array.isArray(evidence.measured) ? { measured: evidence.measured.map(String) } : undefined;
  return (
    <ul className="flex flex-wrap gap-1.5 text-xs" aria-label="evidence">
      {lignes.map(([nom, valeur, cle]) => (
        <li key={nom} className="flex items-baseline gap-1.5 border border-line px-1.5 py-0.5">
          <span className="text-ink-muted">{nom}</span> {valeur}
          <Provenance de={provenanceDePreuve(mesures, cle)} />
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
      {pas.summary && (
        <p className="text-sm text-ink">
          {pas.summary} <Provenance de="declared" />
        </p>
      )}
      {pas.verdict && (
        <p className={cn("text-sm font-medium", pas.verdict === "approve" ? "text-succeeded-ink" : "text-retrying-ink")}>
          verdict: {pas.verdict.replace(/_/g, " ")} <Provenance de="declared" />
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
              vue === cle ? "border-accent font-medium text-ink" : "border-transparent text-ink-muted hover:text-ink",
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
  const plan = planDeLAgent(evenements);
  return (
    <div className="space-y-3 text-sm" data-testid="ce-qu-il-a-fait">
      {plan && (
        <section>
          <h4 className={cn(LEGENDE, "mb-1")}>agent plan</h4>
          <PlanDeLAgent plan={plan} />
        </section>
      )}
      <section>
        <h4 className={LEGENDE}>gates</h4>
        {garanties.length === 0 ? (
          <p className="text-xs text-ink-muted">No gate evaluated on this run.</p>
        ) : (
          <ul className="space-y-0.5">
            {garanties.map((g, i) => (
              <li key={`${g.name}-${i}`} className="flex gap-2 text-xs">
                <span
                  className={g.pending ? "text-waiting-ink" : g.passed ? "text-succeeded-ink" : "text-failed-ink"}
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
        <h4 className={LEGENDE}>
          access {acces.data ? `· ${acces.data.evenements} events, ${acces.data.refus} refused` : ""}
        </h4>
        {lignes.length === 0 ? (
          <p className="text-xs text-ink-muted">No access recorded.</p>
        ) : (
          <ul className="space-y-0.5 text-xs">
            {[...lignes]
              .sort((a, b) => b.refus - a.refus)
              .map((l) => (
                <li key={`${l.nature}:${l.cible}`} className={cn("flex gap-2", l.refus > 0 && "text-failed-ink")}>
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
        <h4 className={LEGENDE}>
          files changed {diff.data ? `· +${diff.data.additions} −${diff.data.deletions}` : ""}
        </h4>
        {fichiers.length === 0 ? (
          <Empty>no file changed</Empty>
        ) : (
          <ul className="space-y-0.5 text-xs">
            {fichiers.map((f) => (
              <li key={f.path} className="flex gap-2">
                <span className="min-w-0 flex-1 break-all font-mono">{f.path}</span>
                <span className="text-succeeded-ink">+{f.additions}</span>
                <span className="text-failed-ink">−{f.deletions}</span>
                {f.in_scope === false && <span className="text-failed-ink">out of scope</span>}
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
        <div className="border border-line border-l-2 border-l-waiting p-3">
          <p className={cn(LEGENDE, "mb-2")}>decide here</p>
          <DecisionBar itemId={itemId} kind={pas.role ?? "approval"} onDone={onDecided} />
        </div>
      ) : (
        <p className={cn("text-sm", pas.status === "rejected" ? "text-failed-ink" : "text-succeeded-ink")}>
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
