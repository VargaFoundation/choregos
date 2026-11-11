// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { AddToMemory, Decide } from "@/components/assistants/memory-add";
import { Badge, Card, Empty, ErrorNote, ReadState } from "@/components/ui";
import { isProposed } from "@/lib/assistants";
import { assistantsApi, type MemoryFactView } from "@/lib/assistants-api";

type Ref = { org: string; slug: string };
type Body = Parameters<typeof assistantsApi.installationMemoryAction>[3];

function InstallationSpace({ org, slug, space }: Ref & { space: string }) {
  const client = useQueryClient();
  const key = ["installation-memory", org, slug, space];
  const memory = useQuery({ queryKey: key, queryFn: () => assistantsApi.installationMemory(org, slug, space) });
  const [error, setError] = useState<string | null>(null);
  const send = async (body: Body) => {
    await assistantsApi.installationMemoryAction(org, slug, space, body);
    await client.invalidateQueries({ queryKey: key });
  };
  const act = (body: Body) => async () => {
    setError(null);
    try {
      await send(body);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Refused");
    }
  };
  const data = memory.data;
  const facts: MemoryFactView[] = (data?.subjects ?? []).flatMap((s) => s.facts ?? []);
  const profiles = (data?.subjects ?? []).flatMap((s) => s.profiles ?? []);
  return (
    <Card padding="sm" title={space.replaceAll("_", " ")} eyebrow={data ? `writes: ${data.writes}` : undefined}>
      <ReadState lecture={memory} quoi="this memory" />
      {error && <ErrorNote>{error}</ErrorNote>}
      {data && (
        <div className="space-y-3">
          {facts.length === 0 && profiles.length === 0 && <Empty>Nothing in this space yet.</Empty>}
          <ul className="space-y-2">
            {facts.map((fact) => (
              <li key={fact.id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                <span>
                  {fact.content} {isProposed(fact.status) && <Badge tone="waiting">proposed</Badge>}
                </span>
                {isProposed(fact.status) && (
                  <Decide onConfirm={act({ action: "confirm", fact_id: fact.id })} onReject={act({ action: "reject", fact_id: fact.id })} />
                )}
              </li>
            ))}
            {profiles.map((p) => (
              <li key={p.version} className="space-y-1 text-sm">
                <div className="flex items-center justify-between gap-2">
                  <span>
                    Profile v{p.version} <Badge tone={isProposed(p.status) ? "waiting" : "neutral"}>{p.status}</Badge>
                  </span>
                  {isProposed(p.status) && (
                    <Decide onConfirm={act({ action: "confirm", version: p.version })} onReject={act({ action: "reject", version: p.version })} />
                  )}
                </div>
                <pre className="overflow-x-auto rounded bg-surface-muted p-2 text-xs">{JSON.stringify(p.document, null, 2)}</pre>
              </li>
            ))}
          </ul>
          <AddToMemory kind={data.kind} onAdd={(body) => send(body)} />
        </div>
      )}
    </Card>
  );
}

/** The installation's own memory (what every user of the assistant shares), for its owners. */
export function InstallationMemory({ org, slug, spaces }: Ref & { spaces: string[] }) {
  if (spaces.length === 0) return null;
  return (
    <Card title="Installation memory" eyebrow="Shared by everyone it serves: you decide what stays">
      <div className="space-y-3">
        {spaces.map((space) => (
          <InstallationSpace key={space} org={org} slug={slug} space={space} />
        ))}
      </div>
    </Card>
  );
}
