// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import { Button, ErrorNote, Select } from "@/components/ui";
import { api } from "@/lib/api";
import { canControlItems } from "@/lib/memberships";
import { primaryName, repositoryNames } from "@/lib/repositories";
import type { WorkItemDto } from "@/lib/types";
import { workspaceFixed, workspaceOf } from "@/lib/neutral-names";

/**
 * The repository a ticket works on (ADR 0124), in its facts: the one its `fields.repository` names, or
 * the project's primary. While no step has touched it, a person who may control the project's tickets
 * can set it; once fixed, it is said and no longer offered. The API stays the judge (422 for a name the
 * project does not hold, 409 once fixed), and its refusal is shown here.
 */
export function TargetRepository({
  slug,
  item,
  onDone,
}: {
  slug: string;
  item: Pick<WorkItemDto, "id" | "workspace" | "workspace_fixed" | "repository" | "repository_fixed" | "closed_at">;
  onDone: () => unknown;
}) {
  const id = useId();
  const project = useQuery({ queryKey: ["project", slug], queryFn: () => api.project(slug) });
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.me(), retry: false });
  const [chosen, setChosen] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const current = workspaceOf(item);
  const fixed = workspaceFixed(item);
  if (!current) return null;

  const config = project.data?.config;
  const names = repositoryNames(config);
  const primary = primaryName(config);
  const settable =
    !fixed &&
    !item.closed_at &&
    names.length > 1 &&
    project.data !== undefined &&
    me.data !== undefined &&
    canControlItems(me.data.memberships ?? [], project.data.org, project.data.slug);
  const target = chosen ?? current;

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await api.updateWorkItem(item.id, { repository: target });
      setChosen(null);
      await onDone();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "refused");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <dt className="text-ink-muted">Repository</dt>
      <dd className="min-w-0" data-testid="ticket-repository">
        {settable ? (
          <span className="flex flex-wrap items-center gap-2">
            <label htmlFor={`${id}-repository`} className="sr-only">
              Repository
            </label>
            <Select
              id={`${id}-repository`}
              value={target}
              onChange={(event) => setChosen(event.target.value)}
              className="w-auto"
            >
              {names.map((name) => (
                <option key={name} value={name}>
                  {name === primary ? `${name} (primary)` : name}
                </option>
              ))}
            </Select>
            {target !== current && (
              <Button size="sm" onClick={() => void save()} disabled={busy}>
                Set the repository
              </Button>
            )}
          </span>
        ) : (
          <span>
            <span className="font-mono">{current}</span>
            {current === primary && names.length > 1 && <span className="text-ink-muted"> (primary)</span>}
            {fixed && names.length > 1 && (
              <span className="block text-xs text-ink-muted">Fixed: a step already worked on it.</span>
            )}
          </span>
        )}
        {error && <ErrorNote>{error}</ErrorNote>}
      </dd>
    </>
  );
}
