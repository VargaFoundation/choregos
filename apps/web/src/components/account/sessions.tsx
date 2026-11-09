// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Monitor, Smartphone, Tablet } from "lucide-react";
import { ConfirmedGesture } from "@/components/confirmed-gesture";
import { Badge, Card, ReadState } from "@/components/ui";
import { api } from "@/lib/api";
import { relative, shortDate } from "@/lib/format";
import type { BrowserSession } from "@/lib/types";
import { browserLabel, describeBrowser } from "@/lib/user-agent";

const DEVICE_ICONS = { computer: Monitor, phone: Smartphone, tablet: Tablet } as const;

/** How a session was opened, in words. */
const CHANNELS: Record<string, string> = {
  oidc: "single sign-on",
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
  const sessions = useQuery({ queryKey: ["my-sessions"], queryFn: () => api.mySessions() });
  const list = sessions.data ?? [];
  const others = list.filter((s) => !s.current);
  const listed = list.some((s) => s.current);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["my-sessions"] });

  return (
    <Card
      title="Sessions"
      id="sessions"
      className={className}
      action={
        others.length > 0 && (
          <ConfirmedGesture
            tonDuBouton="danger"
            question={`Sign out of ${others.length === 1 ? "your other session" : `your ${others.length} other sessions`}? Each of those browsers is asked to sign in again at its next click; this one stays signed in.`}
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
      {sessions.isSuccess && !listed && (
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
  const Icon = DEVICE_ICONS[device];
  const label = session.user_agent ? browserLabel(session.user_agent) : "A browser not seen yet";
  const facts = [
    session.ip,
    `signed in ${shortDate(session.created_at)} with ${CHANNELS[session.channel] ?? session.channel}`,
    session.current ? "active now" : `active ${relative(session.last_seen_at ?? session.created_at)}`,
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
