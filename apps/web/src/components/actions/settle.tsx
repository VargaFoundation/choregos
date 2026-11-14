// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button, Code, ErrorNote, Textarea } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { shortDate } from "@/lib/format";
import type { Action } from "@/lib/types";

type Outcome = "made" | "not_made";

interface Settlement {
  position: number;
  outcome: Outcome;
  by: string;
  at: string;
  note: string;
  result?: Record<string, unknown> | null;
}

/** What a person recorded once the action was in doubt, if anyone did (S65-26). */
export function settlementOf(action: Action): Settlement | null {
  const record = (action as { settlement?: Settlement | null }).settlement;
  return record ?? null;
}

/** The stated result, read from the text a person typed: an object, or nothing; anything else is refused here. */
export function parseResult(text: string): { ok: true; value: Record<string, unknown> | null } | { ok: false } {
  if (!text.trim()) return { ok: true, value: null };
  try {
    const value: unknown = JSON.parse(text);
    if (typeof value === "object" && value !== null && !Array.isArray(value)) {
      return { ok: true, value: value as Record<string, unknown> };
    }
  } catch {
    // falls through to the refusal
  }
  return { ok: false };
}

/** The API's refusal, as it words it, with the code of a 409 when it carries one. */
function refusal(cause: unknown): string {
  if (cause instanceof ApiError) {
    const code = cause.problem.code;
    const detail = cause.problem.detail ?? cause.problem.title ?? cause.message;
    return code ? `${detail} (${code})` : detail;
  }
  return cause instanceof Error ? cause.message : "refused";
}

/**
 * Settling an action in doubt (ADR 0149 §5, S65-26): a person checked the system the write was for and records what
 * they found. `made` records the effect done, with the result they read there if they give one, and the action
 * resumes after it; `not_made` records it not made, and the action fails, undoing what it had done before. Guarded as
 * an approval: a recent sign-in (the API's `step_up_required` goes back through the IdP and returns here), an
 * approver's rank, and whoever proposed the action does not settle it. The note is required.
 */
export function SettleInDoubt({ projet, action }: { projet: string; action: Action }) {
  const client = useQueryClient();
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [note, setNote] = useState("");
  const [resultText, setResultText] = useState("");
  const [erreur, setErreur] = useState<string | null>(null);
  const [occupe, setOccupe] = useState(false);
  if (action.status !== "in_doubt" || settlementOf(action)) return null;
  const parsed = parseResult(resultText);
  const ready = outcome !== null && note.trim().length > 0 && (outcome === "not_made" || parsed.ok) && !occupe;
  async function settle() {
    if (outcome === null) return;
    setErreur(null);
    setOccupe(true);
    try {
      const body =
        outcome === "made" && parsed.ok && parsed.value
          ? { outcome, note: note.trim(), result: parsed.value }
          : { outcome, note: note.trim() };
      await api.settleAction(projet, action.id, body);
      await client.invalidateQueries({ queryKey: ["action", projet, action.id] });
      await client.invalidateQueries({ queryKey: ["actions", projet] });
      await client.invalidateQueries({ queryKey: ["org-actions"] });
    } catch (cause) {
      setErreur(refusal(cause));
    } finally {
      setOccupe(false);
    }
  }
  return (
    <div className="space-y-3" data-testid="settle">
      <fieldset className="space-y-1">
        <legend className="text-sm font-medium">What did you find in the system the write was for?</legend>
        <label className="flex items-start gap-2 text-sm">
          <input
            type="radio"
            name="settle-outcome"
            value="made"
            checked={outcome === "made"}
            onChange={() => setOutcome("made")}
            className="mt-1"
          />
          <span>
            <strong>The write was made.</strong> The effect is recorded done and the action resumes: the effects after it
            run.
          </span>
        </label>
        <label className="flex items-start gap-2 text-sm">
          <input
            type="radio"
            name="settle-outcome"
            value="not_made"
            checked={outcome === "not_made"}
            onChange={() => setOutcome("not_made")}
            className="mt-1"
          />
          <span>
            <strong>The write was not made.</strong> The action fails, and what it had done before is undone.
          </span>
        </label>
      </fieldset>
      <label className="block space-y-1">
        <span className="text-xs text-ink-muted">What you checked, and where (required)</span>
        <Textarea
          className="w-full"
          rows={2}
          maxLength={2000}
          value={note}
          onChange={(event) => setNote(event.target.value)}
        />
      </label>
      {outcome === "made" && (
        <label className="block space-y-1">
          <span className="text-xs text-ink-muted">
            What the write returned, as you read it there (optional, a JSON object): the effects after it may cite it
          </span>
          <Textarea
            className="w-full font-mono text-xs"
            rows={2}
            value={resultText}
            onChange={(event) => setResultText(event.target.value)}
            aria-invalid={!parsed.ok}
          />
          {!parsed.ok && <span className="text-xs text-danger">Not a JSON object.</span>}
        </label>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button tone="primary" onClick={() => void settle()} disabled={!ready}>
          Record what I found
        </Button>
        <p className="text-xs text-ink-muted" data-testid="settle-rules">
          {action.approval?.step_up_minutes != null
            ? `Needs a recent sign-in (within ${String(action.approval.step_up_minutes)} min), as approving does. `
            : ""}
          Whoever proposed the action, or owns the agent that did, cannot settle it.
        </p>
      </div>
      {erreur && <ErrorNote>{erreur}</ErrorNote>}
    </div>
  );
}

/** What a person recorded, once they did: shown on the action's page in place of the form. */
export function Settled({ action }: { action: Action }) {
  const record = settlementOf(action);
  if (!record) return null;
  return (
    <div className="space-y-1 text-sm" data-testid="settled">
      <p>
        <strong>{record.outcome === "made" ? "Recorded made" : "Recorded not made"}</strong> by {record.by}
        {record.at ? ` · ${shortDate(record.at)}` : ""} — effect {record.position}
      </p>
      <p className="text-ink-muted">{record.note}</p>
      {record.result && Object.keys(record.result).length > 0 && (
        <p className="text-xs">
          Result stated: <Code>{JSON.stringify(record.result)}</Code>
        </p>
      )}
    </div>
  );
}
