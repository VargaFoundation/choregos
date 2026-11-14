// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import { ConfirmedGesture } from "@/components/confirmed-gesture";
import { Input, Select } from "@/components/ui";
import { api } from "@/lib/api";
import { canReassignRequests } from "@/lib/memberships";
import type { HumanRequest, HumanRequestAssignee } from "@/lib/types";

/** Who a waiting request is addressed to, in words: a person, a group, or a person first (ADR 0153). */
export function assigneeInWords(assignee: HumanRequestAssignee | null | undefined): string | null {
  if (!assignee) return null;
  const { user, group } = assignee;
  if (user && group) return `${user}, then the group ${group} once the SLA has passed`;
  if (user) return user;
  if (group) return `the group ${group}`;
  return null;
}

/**
 * "Assigned to …" on a waiting human request (ADR 0153), and, for an organisation administrator,
 * "Reassign": a person of the organisation, a group, or both, with a reason kept in the audit log. The
 * API stays the judge — a person outside the organisation, a request already decided are refused there,
 * and the refusal is shown here.
 */
export function RequestAssignee({
  slug,
  itemId,
  request,
  onDone,
}: {
  slug: string;
  itemId: string;
  request: Pick<HumanRequest, "id" | "assignee" | "decided_at">;
  onDone: () => unknown;
}) {
  const id = useId();
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.me(), retry: false });
  const project = useQuery({ queryKey: ["project", slug], queryFn: () => api.project(slug) });
  const org = project.data?.org ?? "";
  const admin = !!me.data && !!org && canReassignRequests(me.data.memberships ?? [], org);
  const members = useQuery({ queryKey: ["members", org], queryFn: () => api.members(org), enabled: admin });
  const [user, setUser] = useState("");
  const [group, setGroup] = useState("");

  const who = assigneeInWords(request.assignee);
  const emails = [...new Set((members.data ?? []).map((m) => m.email).filter((e): e is string => !!e))].sort();

  return (
    <div className="mb-3 flex flex-wrap items-center gap-2 text-sm" data-testid="request-assignee">
      {who && (
        <span>
          Assigned to <strong>{who}</strong>
          {request.assignee?.reassigned ? <span className="text-ink-muted"> (reassigned)</span> : null}
        </span>
      )}
      {admin && !request.decided_at && (
        <ConfirmedGesture
          ton="primary"
          question={
            <span className="flex flex-wrap items-end gap-2">
              <label className="flex flex-col gap-1">
                <span className="text-xs text-ink-muted">Person</span>
                <Select
                  id={`${id}-user`}
                  aria-label="Person"
                  value={user}
                  onChange={(event) => setUser(event.target.value)}
                  className="w-auto"
                >
                  <option value="">nobody</option>
                  {emails.map((email) => (
                    <option key={email} value={email}>
                      {email}
                    </option>
                  ))}
                </Select>
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-xs text-ink-muted">Group</span>
                <Input
                  aria-label="Group"
                  value={group}
                  onChange={(event) => setGroup(event.target.value)}
                  placeholder="release-captains"
                  className="w-auto"
                />
              </label>
              <span className="block basis-full text-ink-muted">
                With both, the person is asked first and the group may answer once the SLA has passed.
              </span>
            </span>
          }
          confirmer="Reassign"
          reason={{ label: "Why (kept in the audit log)", required: true }}
          action={(reason) => {
            if (!user && !group.trim()) return Promise.reject(new Error("choose a person, a group, or both"));
            return api.reassign(itemId, request.id, {
              user: user || null,
              group: group.trim() || null,
              reason,
            });
          }}
          onDone={() => {
            setUser("");
            setGroup("");
            void onDone();
          }}
        >
          Reassign
        </ConfirmedGesture>
      )}
    </div>
  );
}
