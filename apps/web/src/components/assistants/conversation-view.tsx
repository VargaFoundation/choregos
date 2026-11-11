// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ThumbsDown, ThumbsUp } from "lucide-react";
import Link from "next/link";
import { useRef, useState } from "react";
import { MemoryProposals } from "@/components/assistants/assistant-memory";
import { MarkdownView } from "@/components/markdown-view";
import { OrgPageHeader } from "@/components/org-page-header";
import { Alert, Badge, Button, Card, ErrorNote, ReadState, Textarea, buttonClasses } from "@/components/ui";
import { citationsOf, connectLinksOf, type ConnectLink } from "@/lib/assistants";
import { ActionDiff } from "@/components/assistants/action-diff";
import { assistantsApi, streamTurn, type ConversationAction } from "@/lib/assistants-api";
import { useSession } from "@/lib/session";

/** The links to connect an account that an answer asks for. */
function ConnectLinks({ links }: { links: ConnectLink[] }) {
  if (links.length === 0) return null;
  return (
    <div className="space-y-1">
      {links.map((link) => (
        <Alert key={link.href} tone="warn" title="An account is not connected">
          <Link href={link.href} className={buttonClasses("secondary", "sm")}>
            Connect your {link.connector} account
          </Link>
        </Alert>
      ))}
    </div>
  );
}

/** One answer: rendered markdown, its citations, its connect links, and a thumb with a comment. */
function Answer({ text, session, org, last }: { text: string; session: string; org: string; last: boolean }) {
  const citations = citationsOf(text);
  const [rating, setRating] = useState<"up" | "down" | null>(null);
  const [comment, setComment] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function send() {
    if (!rating) return;
    setError(null);
    try {
      await assistantsApi.feedback(org, session, { rating, comment: comment.trim() || null });
      setSent(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Feedback refused");
    }
  }
  return (
    <div className="space-y-2">
      <MarkdownView text={text} />
      {citations.length > 0 && (
        <ul className="flex flex-wrap gap-2" aria-label="Citations">
          {citations.map((c) => (
            <li key={`${c.title}@${c.version}`}>
              <Badge tone="info">
                {c.title} · v{c.version}
              </Badge>
            </li>
          ))}
        </ul>
      )}
      <ConnectLinks links={connectLinksOf(text)} />
      {last && (
        <div className="space-y-2">
          {sent ? (
            <p className="text-xs text-ink-muted">Thank you for your feedback.</p>
          ) : (
            <div className="flex items-center gap-1">
              <Button size="sm" tone={rating === "up" ? "primary" : "default"} aria-label="Good answer" aria-pressed={rating === "up"} onClick={() => setRating("up")}>
                <ThumbsUp size={14} />
              </Button>
              <Button size="sm" tone={rating === "down" ? "primary" : "default"} aria-label="Bad answer" aria-pressed={rating === "down"} onClick={() => setRating("down")}>
                <ThumbsDown size={14} />
              </Button>
            </div>
          )}
          {rating && !sent && (
            <div className="space-y-2">
              <Textarea aria-label="Comment" placeholder="A comment (optional)" value={comment} onChange={(e) => setComment(e.target.value)} />
              <Button size="sm" onClick={() => void send()}>
                Send feedback
              </Button>
            </div>
          )}
          {error && <ErrorNote>{error}</ErrorNote>}
        </div>
      )}
    </div>
  );
}

/** A write the assistant proposes: confirm or refuse it, nothing is written before. */
function ActionCard({ action, conversation, onDecided }: { action: ConversationAction; conversation: string; onDecided: () => void }) {
  const [error, setError] = useState<string | null>(null);
  async function decide(kind: "confirm" | "refuse") {
    setError(null);
    try {
      await (kind === "confirm"
        ? assistantsApi.confirmAction(conversation, action.id)
        : assistantsApi.refuseAction(conversation, action.id));
      onDecided();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Refused");
    }
  }
  return (
    <Card padding="sm" title="The assistant wants to act" eyebrow={`${action.need} · ${action.operation}`}>
      <p className="text-sm">{action.summary}</p>
      <pre className="mt-2 overflow-x-auto rounded bg-surface-muted p-2 text-xs">{JSON.stringify(action.arguments, null, 2)}</pre>
      <ActionDiff diff={action.diff} />
      {error && <ErrorNote>{error}</ErrorNote>}
      <div className="mt-3 flex gap-2">
        <Button size="sm" tone="primary" onClick={() => void decide("confirm")}>
          Confirm
        </Button>
        <Button size="sm" tone="default" onClick={() => void decide("refuse")}>
          Refuse
        </Button>
      </div>
    </Card>
  );
}

/**
 * A conversation: what was said, the answer as it streams, cancel, thumbs, the writes waiting for me
 * and what the assistant proposes to remember. `assistant` (`org/slug`) tells which memory to show.
 */
export function ConversationView({ id, assistant }: { id: string; assistant?: string }) {
  const { org: sessionOrg } = useSession();
  const client = useQueryClient();
  const conversation = useQuery({ queryKey: ["conversation", id], queryFn: () => assistantsApi.conversation(id) });
  const actions = useQuery({ queryKey: ["conversation-actions", id], queryFn: () => assistantsApi.conversationActions(id) });
  const mine = useQuery({ queryKey: ["my-assistants"], queryFn: assistantsApi.myAssistants });
  // The conversation carries its organisation and its assistant; `?assistant=org/slug` stays as a hint
  // for a conversation read before the API said it.
  const [hintOrg, hintSlug] = (assistant ?? "").split("/");
  const org = conversation.data?.org ?? hintOrg;
  const slug = conversation.data?.assistant ?? hintSlug;
  const known = (mine.data?.items ?? []).find((a) => a.org === org && a.slug === slug);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [streamed, setStreamed] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [missing, setMissing] = useState<ConnectLink[]>([]);
  const abort = useRef<AbortController | null>(null);

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setPending(text);
    setStreamed("");
    setError(null);
    setMissing([]);
    setBusy(true);
    abort.current = new AbortController();
    let acc = "";
    try {
      await streamTurn(
        id,
        text,
        {
          onDelta: (chunk) => {
            acc += chunk;
            setStreamed(acc);
          },
          onDone: (answer) => {
            if (answer.status === "failed") setError("The assistant could not answer.");
            setMissing(connectLinksOf(acc || answer.output_text || "", answer.missing_accounts ?? []));
          },
          onError: setError,
        },
        abort.current.signal,
      );
    } catch (cause) {
      if (!(cause instanceof DOMException && cause.name === "AbortError")) {
        setError(cause instanceof Error ? cause.message : "The turn failed");
      }
    } finally {
      setBusy(false);
      setPending(null);
      setStreamed("");
      await Promise.all([
        client.invalidateQueries({ queryKey: ["conversation", id] }),
        client.invalidateQueries({ queryKey: ["conversation-actions", id] }),
        client.invalidateQueries({ queryKey: ["assistant-memory"] }),
      ]);
    }
  }

  async function cancel() {
    abort.current?.abort();
    try {
      await assistantsApi.cancelTurn(id);
    } catch {
      // the turn may have ended already: nothing to cancel
    }
  }

  const messages = conversation.data?.messages ?? [];
  const lastAnswer = messages.map((m) => m.role).lastIndexOf("assistant");
  const waiting = (actions.data?.items ?? []).filter((a) => a.status === "proposed");
  return (
    <div className="space-y-6">
      <OrgPageHeader
        title={conversation.data?.title ?? "Conversation"}
        eyebrow="Assistants"
        description={conversation.data ? `${conversation.data.agent} · ${conversation.data.turns} turns` : undefined}
        actions={
          <Link href="/conversations" className={buttonClasses("secondary")}>
            All conversations
          </Link>
        }
      />
      <ReadState lecture={conversation} quoi="this conversation" />
      <div className="space-y-4" aria-live="polite">
        {messages.map((m, index) => (
          <Card key={index} padding="sm" eyebrow={m.role === "user" ? "You" : "Assistant"}>
            {m.role === "user" ? (
              <p className="whitespace-pre-wrap text-sm">{m.content}</p>
            ) : (
              <Answer text={m.content} session={id} org={org || sessionOrg} last={index === lastAnswer && !busy} />
            )}
          </Card>
        ))}
        {pending && (
          <Card padding="sm" eyebrow="You">
            <p className="whitespace-pre-wrap text-sm">{pending}</p>
          </Card>
        )}
        {busy && (
          <Card padding="sm" eyebrow="Assistant">
            {streamed ? <MarkdownView text={streamed} /> : <p className="text-sm text-ink-muted">Thinking…</p>}
          </Card>
        )}
      </div>
      <ConnectLinks links={missing} />
      {error && <ErrorNote>{error}</ErrorNote>}
      {waiting.map((a) => (
        <ActionCard key={a.id} action={a} conversation={id} onDecided={() => void actions.refetch()} />
      ))}
      {known && <MemoryProposals org={known.org} slug={known.slug} spaces={(known.memory_spaces ?? []).map((s) => s.name)} />}
      <form
        className="space-y-2"
        onSubmit={(event) => {
          event.preventDefault();
          void send();
        }}
      >
        <Textarea aria-label="Message" placeholder="Ask something…" value={input} onChange={(e) => setInput(e.target.value)} disabled={busy} />
        <div className="flex gap-2">
          <Button type="submit" tone="primary" disabled={busy || !input.trim()}>
            Send
          </Button>
          {busy && (
            <Button type="button" tone="default" onClick={() => void cancel()}>
              Cancel
            </Button>
          )}
        </div>
      </form>
    </div>
  );
}
