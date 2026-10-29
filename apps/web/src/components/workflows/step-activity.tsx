// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { LEGENDE } from "@/components/ui";
import { api } from "@/lib/api";
import { usd } from "@/lib/format";
import type { WorkflowActivity } from "@/lib/types";

/** A workflow's activity (S47-39): absent while it loads, and against an API without the endpoint. */
export function useWorkflowActivity(slug: string, name: string | undefined) {
  return useQuery({
    queryKey: ["workflow-activity", slug, name],
    queryFn: () => api.workflowActivity(slug, name!),
    enabled: Boolean(name),
    retry: false,
    staleTime: 60_000,
  });
}

/** How many open work items wait in each state, for the map's labels. */
export function openItemsByState(activity: WorkflowActivity | undefined): Map<string, number> {
  return new Map((activity?.states ?? []).map((s) => [s.state, s.open_items]));
}

/** A wait in words: "40 min", "5 h", "3 d". */
export function waitInWords(since: string, now: number = Date.now()): string {
  const minutes = Math.max(0, Math.round((now - Date.parse(since)) / 60_000));
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.round(minutes / 60);
  return hours < 48 ? `${hours} h` : `${Math.round(hours / 24)} d`;
}

/**
 * How many open work items wait for a step — in the state it leaves — and for how long the
 * longest has (since it last entered that state); null when none waits.
 */
export function waitingSentence(
  activity: WorkflowActivity | undefined,
  state: string,
  display: string,
  now: number = Date.now(),
): string | null {
  const here = activity?.states?.find((s) => s.state === state);
  if (!here || here.open_items === 0) return null;
  const items = `${here.open_items} work item${here.open_items > 1 ? "s" : ""}`;
  const longest = here.waiting_since ? `, the longest for ${waitInWords(here.waiting_since, now)}` : "";
  return `${items} wait${here.open_items > 1 ? "" : "s"} in “${display}”${longest}.`;
}

/** What a step's runs did over the period, in one line; null when it never ran. */
export function stepActivitySentence(activity: WorkflowActivity | undefined, transition: string): string | null {
  const step = activity?.steps?.find((s) => s.transition === transition);
  if (!activity) return null;
  if (!step || step.runs === 0) return `No run in the last ${activity.days} days.`;
  const parts = [
    `${step.runs} run${step.runs > 1 ? "s" : ""}`,
    `${step.succeeded} succeeded`,
    step.failed ? `${step.failed} failed` : null,
    usd(step.cost_usd),
    step.median_minutes != null ? `median ${step.median_minutes} min` : null,
  ].filter(Boolean);
  return `Last ${activity.days} days: ${parts.join(" · ")}.`;
}

/**
 * Who a person step waits for (ADR 0044): the members carrying its group, and which of them may answer
 * in this project. A group nobody carries is said plainly — the step then waits for an organisation
 * administrator. Silent when the reader may not list members (403) or the API predates the endpoint.
 */
export function GroupMembersLine({ projectSlug, group }: { projectSlug: string; group: string }) {
  const lu = useQuery({
    queryKey: ["group-members", projectSlug, group],
    queryFn: () => api.groupMembers(projectSlug, group),
    retry: false,
    staleTime: 60_000,
  });
  if (!lu.data) return null;
  const members = lu.data.members ?? [];
  const deciders = members.filter((m) => m.can_decide);
  return (
    <div className="grid grid-cols-[auto_1fr] gap-x-4 text-xs" data-testid="group-members">
      <span className={LEGENDE}>In the group</span>
      {members.length === 0 ? (
        <span className="text-retrying-ink">
          nobody carries <code>{group}</code> (identity-provider groups, as of each member&apos;s last sign-in): a step
          addressed to it waits for an organisation administrator
        </span>
      ) : (
        <span>
          {members.map((m, index) => (
            <span key={m.email}>
              {index > 0 && ", "}
              <span className={m.can_decide ? "text-ink" : "text-ink-muted line-through"} title={m.email}>
                {m.display_name || m.email}
              </span>
            </span>
          ))}
          {deciders.length < members.length && (
            <span className="text-ink-muted"> — struck through: in the group, but their role here cannot answer</span>
          )}
          {deciders.length === 0 && (
            <span className="block text-retrying-ink">nobody of it may answer in this project: an organisation administrator will have to</span>
          )}
        </span>
      )}
    </div>
  );
}
