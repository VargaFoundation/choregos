// SPDX-License-Identifier: Apache-2.0
"use client";

import { Circle, CircleCheck, CircleDot, CircleMinus } from "lucide-react";
import { cn } from "@/lib/cn";
import { shortDate } from "@/lib/format";
import type { PlanDeLAgent as Plan, StatutDeTache, TacheDuPlan } from "@/lib/plan-de-l-agent";

const LIBELLE: Record<StatutDeTache, string> = { pending: "to do", in_progress: "in progress", completed: "done" };

/**
 * The agent's plan, live (S25-01): what it set itself to do and where it stands, and what it
 * dropped along the way, struck through, never counted as done. Progress counts only the current plan.
 */
export function AgentPlanView({ plan }: { plan: Plan }) {
  const latest = plan.revisions.at(-1)!;
  return (
    <div className="space-y-2" data-testid="plan-de-l-agent">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 text-xs text-ink-muted">
        <span data-testid="plan-progression">
          <span className="font-mono tabular-nums text-ink">
            {plan.faites} of {plan.total}
          </span>{" "}
          done
        </span>
        <span className="font-mono">
          Revision {plan.revisions.length} · {shortDate(latest.ts)}
        </span>
      </div>
      <div
        role="progressbar"
        aria-label="Tasks done"
        aria-valuemin={0}
        aria-valuemax={plan.total}
        aria-valuenow={plan.faites}
        className="h-1 bg-surface-muted"
      >
        <div
          className="h-full bg-succeeded transition-[width] duration-500"
          style={{ width: `${plan.total ? (plan.faites / plan.total) * 100 : 0}%` }}
        />
      </div>
      <ol aria-label="Agent plan" className="space-y-1.5 text-sm">
        {plan.taches.map((tache) => (
          <Row key={`${tache.retiree ? "retiree" : "courante"}:${tache.contenu}`} tache={tache} />
        ))}
      </ol>
    </div>
  );
}

function Row({ tache }: { tache: TacheDuPlan }) {
  if (tache.retiree) {
    return (
      <li className="flex items-start gap-2 text-ink-muted" data-statut="removed">
        <CircleMinus aria-hidden className="mt-0.5 size-4 shrink-0" strokeWidth={1.75} />
        <span className="min-w-0">
          <span className="line-through">{tache.contenu}</span>{" "}
          <span className="font-mono text-[11px]">
            removed in revision {tache.revision}
            {tache.avant === "completed" ? ", after being marked done" : ""}
          </span>
        </span>
      </li>
    );
  }
  const Icone = { pending: Circle, in_progress: CircleDot, completed: CircleCheck }[tache.statut];
  return (
    <li className="flex items-start gap-2" data-statut={tache.statut}>
      <Icone
        aria-hidden
        className={cn(
          "mt-0.5 size-4 shrink-0",
          tache.statut === "completed" && "text-succeeded-ink",
          tache.statut === "in_progress" && "text-running-ink motion-safe:animate-pulse",
          tache.statut === "pending" && "text-ink-subtle",
        )}
        strokeWidth={1.75}
      />
      <span className={cn("min-w-0", tache.statut === "completed" ? "text-ink-muted" : "text-ink")}>
        {tache.contenu}
        <span className="sr-only"> — {LIBELLE[tache.statut]}</span>
      </span>
      {tache.priorite === "high" && (
        <span className="ml-auto shrink-0 font-mono text-[11px] text-ink-muted">High</span>
      )}
    </li>
  );
}
export { AgentPlanView as PlanDeLAgent }; // alias:internal (S45-28): the journey panel still imports the old name
