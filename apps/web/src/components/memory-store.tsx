// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { api } from "@/lib/api";
import { orgConnectorHref, projectConnectionsHref } from "@/lib/connector-link";

/**
 * Which store holds a memory, and the connector behind it (owner review of 2026-10-28). The rules are
 * the server's (`services/memory_access.py`): a project's memories live in its `memory` connector, else
 * the deployment's default store; the organisation's in its first `memory` connector by name, else the
 * default.
 */
export function ProjectMemoryStore({ slug }: { slug: string }) {
  const connectors = useQuery({ queryKey: ["connectors", slug], queryFn: () => api.connectors(slug) });
  if (!connectors.data) return null;
  const memory = connectors.data.find((c) => c.kind === "memory");
  return (
    <p className="text-xs" data-testid="memory-store">
      Stored in{" "}
      {memory ? (
        <>
          the project&apos;s{" "}
          <Link href={projectConnectionsHref(slug)} className="underline">
            memory connector
          </Link>{" "}
          <span className="text-ink-muted">({memory.type})</span>
        </>
      ) : (
        <span className="text-ink-muted">the deployment&apos;s default memory store (no memory connector in this project)</span>
      )}
      .
    </p>
  );
}

export function OrganisationMemoryStore({ org }: { org: string }) {
  const connectors = useQuery({ queryKey: ["org-connectors", org], queryFn: () => api.orgConnectors(org) });
  // A member without access to the organisation's connectors is told nothing rather than something wrong.
  if (!connectors.data) return null;
  const memory = connectors.data
    .filter((c) => c.kind === "memory")
    .sort((a, b) => a.name.localeCompare(b.name))[0];
  return (
    <p className="text-xs" data-testid="memory-store">
      Stored in{" "}
      {memory ? (
        <>
          the organisation&apos;s memory connector{" "}
          <Link href={orgConnectorHref(memory.name)} className="font-medium underline" data-donnee>
            {memory.name}
          </Link>{" "}
          <span className="text-ink-muted">({memory.type})</span>
        </>
      ) : (
        <span className="text-ink-muted">the deployment&apos;s default memory store (no organisation memory connector)</span>
      )}
      .
    </p>
  );
}
