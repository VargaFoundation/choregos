// SPDX-License-Identifier: Apache-2.0
"use client";

import { useState } from "react";
import { Button, ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";
import { relative } from "@/lib/format";
import type { DecisionDraft, DecisionDraftVia } from "@/lib/types";

/** Who drafted it, in words: the client (a token's name or an OAuth client), and the agent it acts as. */
export function draftedVia(via: DecisionDraftVia): string {
  const client = via.client ? `the client ${via.client}` : "a client";
  return via.agent ? `${client} (external agent ${via.agent})` : client;
}

const KIND_WORDS: Record<DecisionDraft["kind"], string> = {
  approve: "approve",
  reject: "send back",
  answer: "answer",
  complete: "do the task",
};

/**
 * The draft a person's client deposited through the MCP door (ADR 0163): what it proposes, who made it and
 * through what, and its note — written by the client, shown as data. **Take** fills the decision's form below
 * (the person still decides, and attests, there); **Discard** removes the draft, audited. Neither decides.
 */
export function DecisionDraftCard({
  itemId,
  requestId,
  draft,
  onTake,
  onDone,
}: {
  itemId: string;
  requestId: string;
  draft: DecisionDraft;
  onTake: (draft: DecisionDraft) => void;
  onDone: () => unknown;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function discard() {
    setBusy(true);
    setError(null);
    try {
      await api.discardDraft(itemId, requestId);
      await onDone();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "draft not discarded");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="mb-3 rounded-md border border-dashed border-line p-3 text-sm"
      data-testid="decision-draft"
    >
      <p className="mb-1">
        <strong>Draft</strong> — proposes to{" "}
        <strong>{KIND_WORDS[draft.kind]}</strong>
        <span className="text-ink-muted">
          {" "}
          · prepared by {draftedVia(draft.via)} for {draft.by},{" "}
          {relative(draft.at)}
        </span>
      </p>
      <p className="mb-1 break-words" data-testid="decision-draft-summary">
        {draft.summary}
      </p>
      {draft.note && (
        <blockquote
          className="mb-2 border-l-2 border-line pl-2 text-ink-muted"
          data-testid="decision-draft-note"
        >
          {draft.note}
        </blockquote>
      )}
      <p className="mb-2 text-xs text-ink-muted">
        Nothing is decided yet: take the draft into the form below and decide
        there, or discard it.
      </p>
      <div className="flex flex-wrap gap-2">
        <Button disabled={busy} onClick={() => onTake(draft)}>
          Take
        </Button>
        <Button disabled={busy} onClick={discard}>
          Discard
        </Button>
      </div>
      {error && <ErrorNote>{error}</ErrorNote>}
    </div>
  );
}
