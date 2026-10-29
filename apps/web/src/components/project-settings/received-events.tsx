// SPDX-License-Identifier: Apache-2.0
"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import Link from "next/link";
import { Badge, Button, Card, ErrorNote, type Tone } from "@/components/ui";
import { api } from "@/lib/api";
import { relative } from "@/lib/format";
import type { ReceivedEvent } from "@/lib/types";

/** What the platform did with an event, in words (the contract's `ReceivedEvent.outcome`). */
const OUTCOME: Record<string, { label: string; tone: Tone }> = {
  created_work_item: { label: "started a new work item", tone: "ok" },
  started: { label: "started its workflow", tone: "ok" },
  signalled: { label: "passed to its workflow", tone: "neutral" },
  boarded: { label: "boarded the release train", tone: "ok" },
  labels_updated: { label: "labels updated", tone: "neutral" },
  ignored_no_item_key: { label: "ignored: names no work item", tone: "warn" },
  ignored_not_agent_ready: { label: "ignored: not labelled agent-ready", tone: "warn" },
};

/**
 * The webhook events this project received, newest first (owner review of 2026-10-28: "we don't
 * see the webhooks, or at least the events received"). A delivery that names no project, fails its
 * signature or repeats one already seen is not here: it never reached the project.
 */
export function ReceivedEvents({ slug }: { slug: string }) {
  const pages = useInfiniteQuery({
    queryKey: ["received-events", slug],
    queryFn: ({ pageParam }) => api.receivedEvents(slug, pageParam ? { cursor: pageParam, limit: "20" } : { limit: "20" }),
    initialPageParam: "",
    getNextPageParam: (last) => (last.meta.has_more ? (last.meta.next_cursor ?? undefined) : undefined),
  });
  const events = (pages.data?.pages ?? []).flatMap((page) => page.items);
  return (
    <Card title="Received events" data-testid="received-events">
      <p className="mb-3 text-sm text-ink-muted">
        The webhook events the project&apos;s tools sent (tracker, repository, CI, deployment, alerts) and what Choregos
        did with each. Only what reached this project is listed; nothing a person wrote is kept.
      </p>
      {pages.error ? (
        <ErrorNote>{String(pages.error)}</ErrorNote>
      ) : !pages.data ? (
        <p className="text-sm text-ink-muted">Reading…</p>
      ) : events.length === 0 ? (
        <p className="text-sm text-ink-muted" data-testid="received-events-none">
          No event received yet. If the tracker should be sending some, check its webhook in the connector&apos;s setup
          procedure.
        </p>
      ) : (
        <ul className="space-y-2 text-sm">
          {events.map((event) => (
            <Line key={event.id} slug={slug} event={event} />
          ))}
        </ul>
      )}
      {pages.hasNextPage && (
        <div className="mt-3">
          <Button size="sm" onClick={() => void pages.fetchNextPage()} disabled={pages.isFetchingNextPage}>
            Older events
          </Button>
        </div>
      )}
    </Card>
  );
}

function Line({ slug, event }: { slug: string; event: ReceivedEvent }) {
  const outcome = OUTCOME[event.outcome] ?? { label: event.outcome, tone: "neutral" as Tone };
  return (
    <li className="flex flex-wrap items-baseline gap-x-2 gap-y-1 border-t border-line pt-2" data-testid={`received-${event.id}`}>
      <span className="text-xs text-ink-muted" title={event.received_at}>
        {relative(event.received_at)}
      </span>
      <span className="font-medium">{event.source}</span>
      <span className="font-mono text-xs">{event.event_type}</span>
      {event.work_item_id ? (
        <Link href={`/p/${slug}/items/${event.work_item_id}`} className="underline">
          {event.work_item_key}
        </Link>
      ) : (
        event.work_item_key && <span>{event.work_item_key}</span>
      )}
      <Badge tone={outcome.tone}>{outcome.label}</Badge>
      <span className="w-full text-xs text-ink-muted">{event.summary}</span>
    </li>
  );
}
