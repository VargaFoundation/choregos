// SPDX-License-Identifier: Apache-2.0
"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Alert, Button, Input } from "@/components/ui";
import { api, API_BASE, ApiError } from "@/lib/api";
import {
  createEmitter,
  decodeContext,
  readEvents,
  targetOrigins,
  type EmbedEventType,
} from "@/lib/embed";
import { clearEmbedToken, embedHeaders, setEmbedToken, SIGNED_IN_MESSAGE } from "@/lib/embed-token";
import type { Conversation, ConversationAction } from "@/lib/types";

/**
 * The assistant, inside a host application's iframe (S63-12, ADR 0139 point 6): a minimal conversation, no
 * navigation. It opens (or resumes) a conversation of the installation for the signed-in person with the
 * context the host passed, streams the answer, and shows the writes the assistant proposes for the person to
 * confirm. Everything that matters to the host goes out as a `postMessage` to the origin that framed it.
 *
 * Signed out: one silent attempt (OIDC `prompt=none`, the API's `silent=1`), then a "Sign in" button that
 * opens the identity provider in a popup. The console's session cookie is `SameSite=Lax`, so under a host on
 * another site the frame has none: the popup (first party) mints a short-lived frame token and hands it to
 * this frame (`/embed/signed-in`, `lib/embed-token.ts`), which sends it as `X-Choregos-Embed`.
 */
type Phase =
  | { kind: "starting" }
  | { kind: "signin" }
  | { kind: "failed"; message: string }
  | { kind: "ready"; conversation: Conversation };

type Turn = { role: "user" | "assistant"; content: string };

const SILENT_TRIED = "choregos.embed.silent-tried";

function flag(action: "get" | "set" | "clear"): boolean {
  try {
    if (action === "set") window.sessionStorage.setItem(SILENT_TRIED, "1");
    else if (action === "clear") window.sessionStorage.removeItem(SILENT_TRIED);
    else return window.sessionStorage.getItem(SILENT_TRIED) === "1";
  } catch {
    /* a private window: the silent attempt is simply not remembered */
  }
  return false;
}

/** Signed in? A 401 is an answer here, never a redirect: a framed page must not navigate to the console's login. */
async function signedIn(): Promise<boolean> {
  const response = await fetch(`${API_BASE}/me`, {
    credentials: "include",
    cache: "no-store",
    headers: embedHeaders(),
  });
  return response.ok;
}

function storageKey(org: string, installation: string, rawContext: string | null): string {
  return `choregos.embed.${org}.${installation}.${rawContext ?? ""}`;
}

function remembered(key: string): string | null {
  try {
    return window.sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function remember(key: string, id: string): void {
  try {
    window.sessionStorage.setItem(key, id);
  } catch {
    /* a private window: the conversation simply is not resumed */
  }
}

export function EmbeddedAssistant({
  org,
  installation,
  rawContext,
  signinRequired,
  memorySpaces,
}: {
  org: string;
  installation: string;
  rawContext: string | null;
  /** The silent sign-in already ran and the identity provider needs the person. */
  signinRequired: boolean;
  /** The memory spaces to watch for `memory.updated` (comma-separated names in `?memory=`). */
  memorySpaces: string[];
}) {
  const [phase, setPhase] = useState<Phase>({ kind: "starting" });
  const [attempt, setAttempt] = useState(0);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [actions, setActions] = useState<ConversationAction[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const emitRef = useRef<(type: EmbedEventType, payload?: Record<string, unknown>) => void>(() => undefined);
  const root = useRef<HTMLDivElement>(null);

  // Starts: who may frame us, who is signed in, which conversation.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const { embed_origins } = await api.assistantEmbed(org, installation);
        const ancestors = window.location.ancestorOrigins;
        emitRef.current = createEmitter(
          org,
          installation,
          targetOrigins(embed_origins, ancestors?.[0], document.referrer),
          window.parent !== window ? window.parent : null,
        );
        if (!(await signedIn())) {
          if (cancelled) return;
          if (!signinRequired && !flag("get")) {
            // One silent attempt: back here with `?signin=required` when the provider needs the person.
            // Remembered: a frame that cannot keep the cookie it just got must not loop through the IdP.
            flag("set");
            const here = window.location.pathname + window.location.search;
            window.location.assign(api.loginUrl(here, undefined, false, {}, true));
            return;
          }
          setPhase({ kind: "signin" });
          return;
        }
        flag("clear");
        const context = decodeContext(rawContext);
        const key = storageKey(org, installation, rawContext);
        let conversation: Conversation | null = null;
        const previous = remembered(key);
        let resumed = false;
        if (previous) {
          try {
            const found = await api.myConversation(previous);
            if (found.status === "open" || found.status === "idle") {
              conversation = found;
              resumed = true;
            }
          } catch {
            conversation = null;
          }
        }
        conversation ??= await api.openAssistantConversation(org, installation, context);
        if (cancelled) return;
        remember(key, conversation.id);
        setTurns((conversation.messages ?? []).map((m) => ({ role: m.role, content: m.content })));
        setActions((await api.conversationActions(conversation.id)).items);
        setPhase({ kind: "ready", conversation });
        emitRef.current("conversation.started", { conversation_id: conversation.id, resumed });
      } catch (error) {
        if (!cancelled && error instanceof ApiError && error.status === 401) {
          // An expired frame token (or a session that ended): ask the person again, never navigate away.
          clearEmbedToken();
          setPhase({ kind: "signin" });
        } else if (!cancelled) {
          setPhase({
            kind: "failed",
            message: error instanceof Error ? error.message : "The assistant could not be opened.",
          });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [org, installation, rawContext, signinRequired, attempt]);

  // The popup of the sign-in says it is done: start again.
  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      const data: unknown = event.data;
      if (
        event.origin === window.location.origin &&
        typeof data === "object" &&
        data !== null &&
        (data as { type?: unknown }).type === SIGNED_IN_MESSAGE
      ) {
        const { token, expires_in } = data as { token?: unknown; expires_in?: unknown };
        if (typeof token === "string" && typeof expires_in === "number") setEmbedToken(token, expires_in);
        setPhase({ kind: "starting" });
        setAttempt((n) => n + 1);
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  // The host sizes the frame: tell it the content's height whenever it changes.
  useEffect(() => {
    const node = root.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => emitRef.current("resize", { height: Math.ceil(node.scrollHeight) }));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const memoryOf = useCallback(
    async () =>
      JSON.stringify(await Promise.all(memorySpaces.map((s) => api.assistantMemory(org, installation, s).catch(() => null)))),
    [memorySpaces, org, installation],
  );

  const send = useCallback(
    async (conversation: Conversation) => {
      const text = draft.trim();
      if (!text || busy) return;
      setDraft("");
      setBusy(true);
      setProblem(null);
      setTurns((t) => [...t, { role: "user", content: text }, { role: "assistant", content: "" }]);
      const grow = (delta: string) =>
        setTurns((t) => t.map((turn, i) => (i === t.length - 1 ? { ...turn, content: turn.content + delta } : turn)));
      try {
        const before = memorySpaces.length > 0 ? await memoryOf() : "";
        const response = await api.conversationTurn(conversation.id, text);
        if (!response.ok || !response.body) throw new ApiError(response.status, { title: response.statusText });
        for await (const item of readEvents(response.body)) {
          if (item.event === "response.output_text.delta") grow((JSON.parse(item.data) as { delta: string }).delta);
          else if (item.event === "error") {
            throw new Error((JSON.parse(item.data) as { detail?: string }).detail ?? "The turn failed.");
          }
        }
        setActions((await api.conversationActions(conversation.id)).items);
        if (memorySpaces.length > 0 && (await memoryOf()) !== before) {
          emitRef.current("memory.updated", { conversation_id: conversation.id, spaces: memorySpaces });
        }
      } catch (error) {
        setProblem(error instanceof Error ? error.message : "The turn failed.");
      } finally {
        setBusy(false);
      }
    },
    [busy, draft, memoryOf, memorySpaces],
  );

  const decide = useCallback(async (conversation: Conversation, action: ConversationAction, confirm: boolean) => {
    setProblem(null);
    try {
      const done = confirm
        ? await api.confirmConversationAction(conversation.id, action.id)
        : await api.refuseConversationAction(conversation.id, action.id);
      setActions((list) => list.map((a) => (a.id === done.id ? done : a)));
      if (confirm) {
        emitRef.current("action.confirmed", {
          conversation_id: conversation.id,
          action_id: done.id,
          operation: done.operation,
          status: done.status,
        });
      }
    } catch (error) {
      setProblem(error instanceof Error ? error.message : "The decision failed.");
    }
  }, []);

  const proposed = useMemo(() => actions.filter((a) => a.status === "proposed"), [actions]);

  return (
    <div ref={root} className="mx-auto flex max-w-2xl flex-col gap-3 p-3 text-sm">
      {phase.kind === "starting" && <p role="status" className="text-ink-muted">Opening the assistant…</p>}
      {phase.kind === "failed" && <Alert tone="danger" title="The assistant could not be opened">{phase.message}</Alert>}
      {phase.kind === "signin" && (
        <div className="space-y-2">
          <p className="text-ink-muted">Sign in to talk to the assistant.</p>
          <Button
            onClick={() =>
              window.open(
                api.loginUrl(`/embed/signed-in?org=${encodeURIComponent(org)}&installation=${encodeURIComponent(installation)}`),
                "choregos-signin",
                "popup,width=520,height=680",
              )
            }
          >
            Sign in
          </Button>
        </div>
      )}
      {phase.kind === "ready" && (
        <>
          <ol aria-label="Conversation" className="space-y-2">
            {turns.map((turn, index) => (
              <li
                key={index}
                data-role={turn.role}
                className={turn.role === "user" ? "ml-8 rounded bg-surface-sunken p-2" : "mr-8 whitespace-pre-wrap p-2"}
              >
                {turn.content || (busy ? "…" : "")}
              </li>
            ))}
          </ol>
          {proposed.map((action) => (
            <div key={action.id} role="group" aria-label="Action to confirm" className="rounded border border-line p-2">
              <p className="font-medium">{action.summary}</p>
              <p className="text-xs text-ink-muted">{action.operation}</p>
              <div className="mt-2 flex gap-2">
                <Button tone="primary" size="sm" onClick={() => void decide(phase.conversation, action, true)}>
                  Confirm
                </Button>
                <Button size="sm" onClick={() => void decide(phase.conversation, action, false)}>
                  Refuse
                </Button>
              </div>
            </div>
          ))}
          {problem && <Alert tone="danger">{problem}</Alert>}
          <form
            className="flex gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              void send(phase.conversation);
            }}
          >
            <Input
              aria-label="Message"
              className="min-w-0 flex-1"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              disabled={busy}
            />
            <Button type="submit" tone="primary" disabled={busy || draft.trim() === ""}>
              Send
            </Button>
          </form>
        </>
      )}
    </div>
  );
}
