// SPDX-License-Identifier: Apache-2.0
"use client";

import { Circle, CircleCheck, CircleDot, CircleMinus } from "lucide-react";
import { cn } from "@/lib/cn";
import { shortDate } from "@/lib/format";
import type { AgentPlan as Plan, TaskStatus, PlanTask } from "@/lib/agent-plan";

const LIBELLE: Record<TaskStatus, string> = { pending: "to do", in_progress: "in progress", completed: "done" };

/**
 * The agent's plan, live (S25-01): what it set itself to do and where it stands, and what it
 * dropped along the way, struck through, never counted as done. Progress counts only the current plan.
 */
export function AgentPlanView({ plan }: { plan: Plan }) {
  const latest = plan.revisions.at(-1)!;
  return (
    <div className="space-y-2" data-testid="agent-plan">
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
        {plan.tasks.map((task) => (
          <Row key={`${task.retiree ? "retiree" : "courante"}:${task.content}`} task={task} />
        ))}
      </ol>
    </div>
  );
}

function Row({ task }: { task: PlanTask }) {
  if (task.retiree) {
    return (
      <li className="flex items-start gap-2 text-ink-muted" data-status="removed">
        <CircleMinus aria-hidden className="mt-0.5 size-4 shrink-0" strokeWidth={1.75} />
        <span className="min-w-0">
          <span className="line-through">{task.content}</span>{" "}
          <span className="font-mono text-[11px]">
            removed in revision {task.revision}
            {task.avant === "completed" ? ", after being marked done" : ""}
          </span>
        </span>
      </li>
    );
  }
  const Icone = { pending: Circle, in_progress: CircleDot, completed: CircleCheck }[task.status];
  return (
    <li className="flex items-start gap-2" data-status={task.status}>
      <Icone
        aria-hidden
        className={cn(
          "mt-0.5 size-4 shrink-0",
          task.status === "completed" && "text-succeeded-ink",
          task.status === "in_progress" && "text-running-ink motion-safe:animate-pulse",
          task.status === "pending" && "text-ink-subtle",
        )}
        strokeWidth={1.75}
      />
      <span className={cn("min-w-0", task.status === "completed" ? "text-ink-muted" : "text-ink")}>
        {task.content}
        <span className="sr-only"> — {LIBELLE[task.status]}</span>
      </span>
      {task.priorite === "high" && (
        <span className="ml-auto shrink-0 font-mono text-[11px] text-ink-muted">High</span>
      )}
    </li>
  );
}
