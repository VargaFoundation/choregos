// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ConfirmedGesture } from "@/components/confirmed-gesture";
import { Badge, Button, Card, Empty, ErrorNote, ReadState } from "@/components/ui";
import { assistantsApi, type MemoryFactView, type MemorySpace } from "@/lib/assistants-api";
import { isProposed } from "@/lib/assistants";

type Ref = { org: string; slug: string };
type SubjectOf = MemorySpace["subjects"][number];

function subjectKey(s: SubjectOf): string | undefined {
  const subject = s.subject as { key?: string } | null | undefined;
  return subject?.key;
}

function subjectLabel(s: SubjectOf): string {
  const subject = s.subject as { type?: string; key?: string } | null | undefined;
  return subject?.key ? `${subject.type ?? "subject"}: ${subject.key}` : "About you";
}

/** One space's decisions, shared by the memory page and the cards of a conversation. */
function useMemoryActions({ org, slug }: Ref, space: string) {
  const client = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const refresh = () => client.invalidateQueries({ queryKey: ["assistant-memory", org, slug] });
  const guard = (run: () => Promise<unknown>) => async () => {
    setError(null);
    try {
      await run();
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Refused");
    }
  };
  return {
    error,
    fact: (subject: string | undefined, fact: MemoryFactView, action: "confirm" | "reject" | "pin" | "unpin") =>
      guard(() => assistantsApi.memoryAction(org, slug, space, { action, fact_id: fact.id }, subject)),
    profile: (subject: string | undefined, version: number, action: "confirm" | "reject") =>
      guard(() => assistantsApi.memoryAction(org, slug, space, { action, version }, subject)),
    forget: (subject: string | undefined, fact: MemoryFactView) =>
      guard(() => assistantsApi.deleteMemory(org, slug, space, fact.id, subject)),
    forgetAll: (subject: string | undefined) => guard(() => assistantsApi.deleteMemory(org, slug, space, undefined, subject)),
  };
}

function exportSpace(space: MemorySpace) {
  const blob = new Blob([JSON.stringify(space, null, 2)], { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `${space.space}.json`;
  link.click();
  URL.revokeObjectURL(link.href);
}

/** What the assistant remembers of me in one space: confirm, reject, pin, delete, export. */
export function MemorySpaceCard({ org, slug, space }: Ref & { space: string }) {
  const memory = useQuery({ queryKey: ["assistant-memory", org, slug, space], queryFn: () => assistantsApi.memory(org, slug, space) });
  const act = useMemoryActions({ org, slug }, space);
  const data = memory.data;
  return (
    <Card
      title={space.replaceAll("_", " ")}
      action={
        data && (
          <div className="flex gap-2">
            <Button size="sm" tone="default" onClick={() => exportSpace(data)}>
              Export
            </Button>
            <ConfirmedGesture
              question="Forget everything the assistant remembers in this space? This cannot be undone."
              confirmer="Forget all"
              action={act.forgetAll(undefined)}
            >
              Forget all
            </ConfirmedGesture>
          </div>
        )
      }
    >
      <ReadState lecture={memory} quoi="this memory" />
      {act.error && <ErrorNote>{act.error}</ErrorNote>}
      {data && (
        <div className="space-y-4">
          <p className="text-xs text-ink-muted">
            {data.scope === "user_subject" ? "One list per subject" : "About you"} · writes: {data.writes}
          </p>
          {data.subjects.length === 0 && <Empty>Nothing remembered here yet.</Empty>}
          {data.subjects.map((s) => {
            const key = subjectKey(s);
            return (
              <div key={key ?? "self"} className="space-y-2">
                {data.scope === "user_subject" && <h3 className="text-sm font-medium">{subjectLabel(s)}</h3>}
                <ul className="space-y-2">
                  {(s.facts ?? []).map((fact) => (
                    <li key={fact.id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                      <span>
                        {fact.content} {fact.pinned && <Badge tone="info">pinned</Badge>}{" "}
                        {isProposed(fact.status) && <Badge tone="waiting">proposed</Badge>}
                      </span>
                      <span className="flex gap-1">
                        {isProposed(fact.status) && (
                          <>
                            <Button size="sm" tone="primary" onClick={act.fact(key, fact, "confirm")}>
                              Confirm
                            </Button>
                            <Button size="sm" tone="default" onClick={act.fact(key, fact, "reject")}>
                              Reject
                            </Button>
                          </>
                        )}
                        <Button size="sm" tone="default" onClick={act.fact(key, fact, fact.pinned ? "unpin" : "pin")}>
                          {fact.pinned ? "Unpin" : "Pin"}
                        </Button>
                        <Button size="sm" tone="danger" onClick={act.forget(key, fact)}>
                          Delete
                        </Button>
                      </span>
                    </li>
                  ))}
                  {(s.profiles ?? []).map((p) => (
                    <li key={p.version} className="space-y-1 text-sm">
                      <div className="flex items-center justify-between gap-2">
                        <span>
                          Profile v{p.version} <Badge tone={isProposed(p.status) ? "waiting" : "neutral"}>{p.status}</Badge>
                        </span>
                        {isProposed(p.status) && (
                          <span className="flex gap-1">
                            <Button size="sm" tone="primary" onClick={act.profile(key, p.version, "confirm")}>
                              Confirm
                            </Button>
                            <Button size="sm" tone="default" onClick={act.profile(key, p.version, "reject")}>
                              Reject
                            </Button>
                          </span>
                        )}
                      </div>
                      <pre className="overflow-x-auto rounded bg-surface-muted p-2 text-xs">
                        {JSON.stringify(p.document, null, 2)}
                      </pre>
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}

/** The memory page of an assistant: every space it declares. */
export function AssistantMemory({ org, slug, spaces }: Ref & { spaces: string[] }) {
  if (spaces.length === 0) return <Empty title="No memory">This assistant keeps nothing about you.</Empty>;
  return (
    <div className="space-y-4">
      <p className="text-sm text-ink-muted">What the assistant remembers about you. You decide what stays.</p>
      {spaces.map((space) => (
        <MemorySpaceCard key={space} org={org} slug={slug} space={space} />
      ))}
    </div>
  );
}

/** What the assistant proposes to remember, as cards to confirm or reject (a conversation's side). */
export function MemoryProposals({ org, slug, spaces }: Ref & { spaces: string[] }) {
  return (
    <div className="space-y-2" data-testid="memory-proposals">
      {spaces.map((space) => (
        <SpaceProposals key={space} org={org} slug={slug} space={space} />
      ))}
    </div>
  );
}

function SpaceProposals({ org, slug, space }: Ref & { space: string }) {
  const memory = useQuery({
    queryKey: ["assistant-memory", org, slug, space],
    queryFn: () => assistantsApi.memory(org, slug, space),
  });
  const cards: Proposal[] = [];
  for (const s of memory.data?.subjects ?? []) {
    for (const fact of s.facts ?? []) if (isProposed(fact.status)) cards.push({ space, subject: subjectKey(s), fact });
    for (const p of s.profiles ?? [])
      if (isProposed(p.status)) cards.push({ space, subject: subjectKey(s), version: p.version, document: p.document });
  }
  return (
    <>
      {cards.map((card) => (
        <ProposalCard key={`${card.subject}/${card.fact?.id ?? card.version}`} org={org} slug={slug} {...card} />
      ))}
    </>
  );
}

interface Proposal {
  space: string;
  subject?: string;
  fact?: MemoryFactView;
  version?: number;
  document?: unknown;
}

function ProposalCard({ org, slug, space, subject, fact, version, document }: Ref & Proposal) {
  const act = useMemoryActions({ org, slug }, space);
  return (
    <Card padding="sm" title="The assistant proposes to remember" eyebrow={space.replaceAll("_", " ")}>
      <p className="text-sm">{fact ? fact.content : <code>{JSON.stringify(document)}</code>}</p>
      {act.error && <ErrorNote>{act.error}</ErrorNote>}
      <div className="mt-3 flex gap-2">
        {(["confirm", "reject"] as const).map((action) => (
          <Button
            key={action}
            size="sm"
            tone={action === "confirm" ? "primary" : "default"}
            onClick={fact ? act.fact(subject, fact, action) : act.profile(subject, version ?? 0, action)}
          >
            {action === "confirm" ? "Confirm" : "Reject"}
          </Button>
        ))}
      </div>
    </Card>
  );
}
