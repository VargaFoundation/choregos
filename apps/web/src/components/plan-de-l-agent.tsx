// SPDX-License-Identifier: Apache-2.0
"use client";

import { Circle, CircleCheck, CircleDot, CircleMinus } from "lucide-react";
import { cn } from "@/lib/cn";
import { shortDate } from "@/lib/format";
import type { PlanDeLAgent as Plan, StatutDeTache, TacheDuPlan } from "@/lib/plan-de-l-agent";

const LIBELLE: Record<StatutDeTache, string> = { pending: "to do", in_progress: "in progress", completed: "done" };

/**
 * Le plan de l'agent, en direct (S25-01) : ce qu'il s'est donné à faire et où il en est — et ce qu'il
 * a retiré en chemin, barré, jamais compté comme fait. La progression ne compte que le plan courant.
 */
export function PlanDeLAgent({ plan }: { plan: Plan }) {
  const derniere = plan.revisions.at(-1)!;
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
          revision {plan.revisions.length} · {shortDate(derniere.ts)}
        </span>
      </div>
      <div
        role="progressbar"
        aria-label="tasks done"
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
      <ol aria-label="agent plan" className="space-y-1.5 text-sm">
        {plan.taches.map((tache) => (
          <Ligne key={`${tache.retiree ? "retiree" : "courante"}:${tache.contenu}`} tache={tache} />
        ))}
      </ol>
    </div>
  );
}

function Ligne({ tache }: { tache: TacheDuPlan }) {
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
        <span className="ml-auto shrink-0 font-mono text-[11px] text-ink-muted">high</span>
      )}
    </li>
  );
}
