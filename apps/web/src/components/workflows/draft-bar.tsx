// SPDX-License-Identifier: Apache-2.0
"use client";

import { useState } from "react";
import { ConfirmedGesture } from "@/components/confirmed-gesture";
import { Button, ErrorNote } from "@/components/ui";
import { useDraft } from "@/components/workflows/draft";

/**
 * What waits to be published, whichever view it was done in (S21-06): the number of gestures, the
 * validation state, the last diff, undo, discard, publish: one single button for the three views.
 * If another version went by during the editing, the bar says so and lets one choose.
 */
export function DraftBar() {
  const draft = useDraft();
  const [message, setMessage] = useState<string | null>(null);
  if (draft.pending === 0 && !draft.error && !message) return null;
  const { conflict, validation } = draft;
  return (
    <div className="space-y-2 rounded border border-line border-l-2 border-l-accent bg-surface p-3" data-testid="draft">
      {draft.pending > 0 && (
        <div className="flex flex-wrap items-center gap-3">
          <p className="text-sm">
            {draft.pending} change{draft.pending > 1 ? "s" : ""} not published yet
            {draft.resumed && <span className="text-ink-muted"> — kept from your last visit</span>}
            {validation.state === "running" && <span className="text-ink-muted"> — validating…</span>}
            {validation.state === "current" && !draft.valid && (
              <span className="text-danger"> — the workflow would be invalid</span>
            )}
          </p>
          <span className="ml-auto inline-flex gap-2">
            <Button size="sm" onClick={() => void draft.undo()} disabled={draft.busy}>
              Undo
            </Button>
            <ConfirmedGesture
              tonDuBouton="default"
              question={`Discard ${draft.pending} unpublished change${draft.pending > 1 ? "s" : ""}? The workflow goes back to v${draft.definition.data?.version ?? "?"} — undo cannot bring them back.`}
              confirmer="Discard my changes"
              action={draft.reload}
              disabled={draft.busy}
            >
              Discard
            </ConfirmedGesture>
            <Button
              size="sm"
              tone="primary"
              onClick={() => void draft.publish().then(setMessage)}
              disabled={!draft.publishable || Boolean(conflict)}
            >
              Publish v{(draft.definition.data?.version ?? 0) + 1}
            </Button>
          </span>
        </div>
      )}
      {validation.state === "unreachable" && (
        <ErrorNote>
          Could not validate the text: {validation.message} — publishing waits for a verdict.{" "}
          <button type="button" className="underline" onClick={draft.revalidate}>
            Retry
          </button>
        </ErrorNote>
      )}
      {conflict && (
        <div className="space-y-2 rounded border border-warn p-2 text-sm" role="alert" data-testid="conflict">
          <p>
            Someone published a newer version meanwhile: you started from v{conflict.readVersion}, the workflow is now
            v{conflict.publishedVersion}.
          </p>
          <span className="inline-flex gap-2">
            <Button size="sm" onClick={() => void draft.reload()} disabled={draft.busy}>
              Reload v{conflict.publishedVersion} (discard my changes)
            </Button>
            <Button
              size="sm"
              tone="danger"
              onClick={() => void draft.overwrite().then(setMessage)}
              disabled={draft.busy || !draft.valid}
            >
              Publish over v{conflict.publishedVersion}
            </Button>
          </span>
        </div>
      )}
      {draft.notices.map((notices) => (
        <p key={notices} className="text-sm text-warn">
          {notices}
        </p>
      ))}
      {draft.errors.map((issue, index) => (
        <ErrorNote key={index}>{issue.message}</ErrorNote>
      ))}
      {draft.lastDiff && (
        <details>
          <summary className="cursor-pointer text-xs text-ink-muted">Last change</summary>
          <pre className="mt-1 whitespace-pre-wrap break-all text-xs" data-testid="last-diff">
            {draft.lastDiff}
          </pre>
        </details>
      )}
      {draft.error && <ErrorNote>{draft.error}</ErrorNote>}
      {message && (
        <p className="text-sm" role="status">
          {message}
        </p>
      )}
    </div>
  );
}
