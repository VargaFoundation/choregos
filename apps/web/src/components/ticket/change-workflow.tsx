// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import { ConfirmedGesture } from "@/components/confirmed-gesture";
import { Select } from "@/components/ui";
import { api } from "@/lib/api";
import { canControlItems } from "@/lib/memberships";

/**
 * "Change workflow" on a ticket (ADR 0123, S52-12): a person who sees a request land in the wrong
 * workflow moves it to another active workflow of its project, where it starts again at that
 * workflow's initial state. The first re-route also says the routing was wrong, which is what the
 * routing's calibration measures. Offered while the ticket is open and to whoever may control the
 * project's work items; the API stays the judge — a run in progress, an ended ticket, its own
 * workflow are refused there, and the refusal is shown here.
 */
export function ChangeWorkflow({
  slug,
  itemId,
  current,
  ended,
  onDone,
}: {
  slug: string;
  itemId: string;
  /** The workflow the ticket follows now. */
  current: string | null | undefined;
  /** The ticket ended: it can no longer change workflow. */
  ended: boolean;
  onDone: () => unknown;
}) {
  const id = useId();
  // The same reads as the session and the workflows page: one request each, shared through the cache.
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.me(), retry: false, enabled: !ended });
  const project = useQuery({ queryKey: ["project", slug], queryFn: () => api.project(slug), enabled: !ended });
  const workflows = useQuery({ queryKey: ["workflows", slug], queryFn: () => api.workflows(slug), enabled: !ended });
  const [chosen, setChosen] = useState<string | null>(null);

  if (ended || !me.data || !project.data || !workflows.data) return null;
  if (!canControlItems(me.data.memberships ?? [], project.data.org, project.data.slug)) return null;
  const others = workflows.data.map((workflow) => workflow.name).filter((name) => name !== current);
  if (others.length === 0) return null;
  const target = chosen !== null && others.includes(chosen) ? chosen : others[0]!;

  return (
    <ConfirmedGesture
      ton="primary"
      question={
        <>
          <label htmlFor={`${id}-workflow`} className="mr-2">
            Move this ticket to
          </label>
          <Select
            id={`${id}-workflow`}
            value={target}
            onChange={(event) => setChosen(event.target.value)}
            className="w-auto"
          >
            {others.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </Select>
          <span className="mt-1 block text-ink-muted">
            It leaves {current ? <code>{current}</code> : "its workflow"} and starts again at the first step of{" "}
            <code>{target}</code>; a person&apos;s pending request is closed, judging nothing. Refused while an agent runs:
            stop the ticket or let the run finish first.
          </span>
        </>
      }
      confirmer="Change the workflow"
      reason={{ label: "Why (optional, kept in the audit log)" }}
      action={(reason) => api.reroute(itemId, target, reason || undefined)}
      onDone={() => {
        setChosen(null);
        void onDone();
      }}
    >
      Change workflow
    </ConfirmedGesture>
  );
}
