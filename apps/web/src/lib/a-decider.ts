// SPDX-License-Identifier: Apache-2.0
/**
 * What waits for a person in the organisation (S23-02): the tickets stopped on a human request
 * (an approval, a question, a scope change, a task) and the governed actions waiting for approval
 * (ADR 0035).
 *
 * Before, `/approvals` showed only the actions: a ticket stopped on "Spec to approve" was only
 * visible by opening its project's board (review of 07/10, point 5: "no page tells me what is
 * waiting for me"). The API has no organisation-wide read: the project tickets are read one
 * project at a time, under the SAME keys as the board (`["items", slug]`), so a decision taken here
 * refreshes the board, and the other way round.
 */
"use client";

import { useQueries, useQuery } from "@tanstack/react-query";
import { scopeOf } from "@/components/shell/navigation";
import { api } from "./api";
import type { Action, BudgetAlert, EgressRule, HumanRequest, WorkItemDto } from "./types";

export interface Waiting {
  item: WorkItemDto;
  demande: HumanRequest;
  /** The due date has passed: the request will be chased, then escalated. */
  enRetard: boolean;
}

const LIBELLES: Record<string, string> = {
  approval: "approval",
  question: "question",
  scope_change: "scope change",
  task: "task",
};

export function requestLabel(kind: string): string {
  return LIBELLES[kind] ?? kind.replace(/_/g, " ");
}

/** What the request says about itself: its question, its summary, otherwise its kind. */
export function requestSummary(request: HumanRequest): string {
  const payload = request.payload ?? {};
  for (const key of ["question", "summary", "title", "instructions"]) {
    const value = payload[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return requestLabel(request.kind);
}

/**
 * The tickets that wait for someone, in the order to handle them: what is overdue first, then the
 * nearest due date, then what has waited the longest. A request already decided waits for no one.
 */
export function waitingTickets(items: WorkItemDto[], now: number = Date.now()): Waiting[] {
  const dueTime = (a: Waiting) => (a.demande.due_at ? new Date(a.demande.due_at).getTime() : Infinity);
  return items
    .filter((item) => item.pending_request && !item.pending_request.decided_at)
    .map((item) => {
      const request = item.pending_request as HumanRequest;
      return { item, demande: request, enRetard: !!request.due_at && new Date(request.due_at).getTime() < now };
    })
    .sort(
      (a, b) =>
        Number(b.enRetard) - Number(a.enRetard) ||
        dueTime(a) - dueTime(b) ||
        new Date(a.demande.requested_at).getTime() - new Date(b.demande.requested_at).getTime(),
    );
}

/**
 * What waits in ONE project (S39-04): its actions, and the budget alerts of the project itself. An
 * agent's alert stays organisation-level: an agent serves several projects. Pure, so it is tested
 * without React Query.
 */
export function waitingInProject(
  actions: Action[],
  budgets: BudgetAlert[],
  slug: string,
): { actions: Action[]; budgets: BudgetAlert[] } {
  return {
    actions: actions.filter((action) => action.project_slug === slug),
    budgets: budgets.filter((alert) => alert.scope === "project" && alert.subject === slug),
  };
}

/**
 * The inbox's reads, shared by the page and the counters. With `project`,
 * only that project's tickets are read (same query key as its board), and the lists are narrowed.
 */
export function useADecider(org: string, project?: string) {
  const projects = useQuery({
    queryKey: ["projects", org],
    queryFn: () => api.projects(org),
    staleTime: 30_000,
    enabled: !project,
  });
  const slugs = project ? [project] : (projects.data?.items ?? []).map((projet) => projet.slug);
  const tickets = useQueries({
    queries: slugs.map((slug) => ({
      queryKey: ["items", slug],
      queryFn: () => api.allWorkItems(slug),
      staleTime: 30_000,
    })),
  });
  const actions = useQuery({
    queryKey: ["org-actions", org],
    queryFn: () => api.orgActions(org, "pending_approval"),
    staleTime: 30_000,
  });
  // A budget that crossed a threshold waits for a person to have seen it (S29-04).
  const budgets = useQuery({
    queryKey: ["budget-alerts", org],
    queryFn: () => api.budgetAlerts(org),
    staleTime: 30_000,
  });
  // Network access someone asked for (S39-22): organisation-level, so never in a project's inbox.
  const egress = useQuery({
    queryKey: ["egress", org],
    queryFn: () => api.orgEgress(org),
    staleTime: 30_000,
    enabled: !project,
  });
  const read = tickets.flatMap((lecture) => lecture.data?.items ?? []);
  const waiting = waitingTickets(read);
  const narrowed = project
    ? waitingInProject(actions.data ?? [], budgets.data ?? [], project)
    : { actions: actions.data ?? [], budgets: budgets.data ?? [] };
  const egressRequests: EgressRule[] = project ? [] : (egress.data?.requests ?? []);
  const canDecideEgress = !project && !!egress.data?.can_decide;
  return {
    tickets: waiting,
    actions: narrowed.actions,
    budgets: narrowed.budgets,
    /** The requests for network access that wait for an administrator (S39-22). */
    egressRequests,
    canDecideEgress,
    /** The projects whose tickets could not be read: they are not counted, and we say so. */
    projetsIllisibles: slugs.filter((_, index) => tickets[index]?.error),
    erreur: projects.error ?? actions.error ?? budgets.error ?? egress.error ?? null,
    pret:
      (!!project || !!projects.data) &&
      tickets.every((lecture) => !lecture.isPending) &&
      !!actions.data &&
      !!budgets.data &&
      (!!project || !!egress.data || !!egress.error),
    // Only those who can decide are told: a request someone else must answer is not their inbox.
    total: waiting.length + narrowed.actions.length + narrowed.budgets.length + (canDecideEgress ? egressRequests.length : 0),
  };
}

/** The inbox count of the scope the path is in: the project's inside a project, the organisation's elsewhere. */
export function useInboxCount(org: string, pathname: string): number {
  const scope = scopeOf(pathname);
  return useADecider(org, scope.kind === "project" ? scope.slug : undefined).total;
}

/** What a budget alert says, in a sentence: whose budget, how far, how much of how much. */
export function phraseDeBudget(alerte: Pick<BudgetAlert, "scope" | "subject" | "threshold" | "spent_usd" | "budget_usd">): string {
  const de = alerte.scope === "agent" ? `Agent ${alerte.subject}` : `Project ${alerte.subject}`;
  const franchi = alerte.threshold >= 100 ? "has spent its whole daily budget" : `has spent ${alerte.threshold} % of its daily budget`;
  return `${de} ${franchi}: $${alerte.spent_usd.toFixed(2)} of $${alerte.budget_usd.toFixed(2)} today.`;
}
