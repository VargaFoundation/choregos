// SPDX-License-Identifier: Apache-2.0
"use client";

import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { Monitor, Smartphone, Tablet, Terminal } from "lucide-react";
import { ConfirmedGesture } from "@/components/confirmed-gesture";
import { Badge, Button, Card, ReadState } from "@/components/ui";
import { api } from "@/lib/api";
import { relative, shortDate } from "@/lib/format";
import type { BrowserSession } from "@/lib/types";
import { browserLabel, describeBrowser } from "@/lib/user-agent";

const DEVICE_ICONS = { computer: Monitor, phone: Smartphone, tablet: Tablet } as const;

/** How a session was opened, in words. */
const CHANNELS: Record<string, string> = {
  oidc: "single sign-on",
  device: "a code approved in the console",
  saml: "SAML single sign-on",
  renewed: "renewed when the other sessions ended",
};

/**
 * Where the person is signed in (ADR 0130): each browser session, the one this page is read in first,
 * and the gestures to end one, or all the others. An ended session is refused at its next request,
 * wherever it is; a copy of its cookie opens nothing.
 */
export function SessionsCard({ className }: { className?: string }) {
  const queryClient = useQueryClient();
  // The server pages the list, most recently active first (ADR 0206); "More" asks for the next page.
  const sessions = useInfiniteQuery({
    queryKey: ["my-sessions", "list"],
    queryFn: ({ pageParam }) => api.mySessions({ cursor: pageParam, limit: 50 }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => (last.meta.has_more ? (last.meta.next_cursor ?? undefined) : undefined),
  });
  // Whether this browser is listed, and whether others exist, are asked of the server: the loaded pages
  // may not hold them.
  const here = useQuery({ queryKey: ["my-sessions", "current"], queryFn: () => api.mySessions({ current: true, limit: 1 }) });
  const others = useQuery({
    queryKey: ["my-sessions", "others"],
    queryFn: () => api.mySessions({ current: false, limit: 2 }),
  });
  const list = sessions.data?.pages.flatMap((page) => page.items) ?? [];
  const otherSessions = others.data?.items ?? [];
  const listed = (here.data?.items.length ?? 0) > 0;
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["my-sessions"] });

  return (
    <Card
      title="Sessions"
      id="sessions"
      className={className}
      action={
        otherSessions.length > 0 && (
          <ConfirmedGesture
            tonDuBouton="danger"
            question={`Sign out of ${otherSessions.length === 1 ? "your other session" : "your other sessions"}? Each of those browsers is asked to sign in again at its next click; this one stays signed in.`}
            confirmer="Sign out of the other sessions"
            action={async () => {
              await api.endOtherSessions();
              await refresh();
            }}
          >
            Sign out of all other sessions
          </ConfirmedGesture>
        )
      }
    >
      <p className="mb-3 text-xs text-ink-muted">
        The browsers where you are signed in. A session lasts eight hours at most; ending one signs that browser
        out at its next click.
      </p>
      <ul aria-label="Your sessions" className="divide-y divide-line">
        {list.map((session) => (
          <SessionRow key={session.id} session={session} onEnded={refresh} />
        ))}
      </ul>
      {sessions.hasNextPage && (
        <div className="mt-3">
          <Button size="sm" onClick={() => void sessions.fetchNextPage()} disabled={sessions.isFetchingNextPage}>
            More
          </Button>
        </div>
      )}
      {sessions.isSuccess && here.isSuccess && !listed && (
        <p className="mt-3 text-xs text-ink-muted">
          This browser signed in before sessions were listed, so it is not shown here. It ends on its own within
          eight hours; signing out of the other sessions keeps it signed in.
        </p>
      )}
      <ReadState lecture={sessions} quoi="your sessions" />
    </Card>
  );
}

function SessionRow({ session, onEnded }: { session: BrowserSession; onEnded: () => Promise<unknown> }) {
  const { device } = describeBrowser(session.user_agent);
  const surface = session.kind === "cli" || session.kind === "editor";
  const surfaceName = session.client_name ?? (session.kind === "cli" ? "Command line" : "Editor");
  const Icon = surface ? Terminal : DEVICE_ICONS[device];
  const label = surface
    ? `${surfaceName} (${session.kind === "cli" ? "command line" : "editor"})`
    : session.user_agent
      ? browserLabel(session.user_agent)
      : "A browser not seen yet";
  const facts = [
    session.ip,
    `signed in ${shortDate(session.created_at)} with ${CHANNELS[session.channel] ?? session.channel}`,
    session.current ? "active now" : `active ${relative(session.last_seen_at ?? session.created_at)}`,
    surface && session.idle_expires_at ? `signs out if idle after ${shortDate(session.idle_expires_at)}` : "",
    `ends ${shortDate(session.expires_at)}`,
  ].filter(Boolean);
  return (
    <li data-testid="session" className="flex items-start gap-3 py-2.5 first:pt-0 last:pb-0">
      <Icon aria-hidden className="mt-0.5 size-4 shrink-0 text-ink-muted" strokeWidth={1.75} />
      <div className="min-w-0 flex-1">
        <p className="flex flex-wrap items-center gap-2 text-sm text-ink">
          <span title={session.user_agent ?? undefined}>{label}</span>
          {session.current && (
            <Badge tone="ok" variant="soft">
              This session
            </Badge>
          )}
        </p>
        <p className="text-xs text-ink-muted">{facts.join(" · ")}</p>
      </div>
      {!session.current && (
        <ConfirmedGesture
          tonDuBouton="danger"
          question={`End this session (${label})? That browser is asked to sign in again at its next click.`}
          confirmer="End the session"
          action={async () => {
            await api.endSession(session.id);
            await onEnded();
          }}
        >
          End
        </ConfirmedGesture>
      )}
    </li>
  );
}
