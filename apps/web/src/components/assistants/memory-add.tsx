// SPDX-License-Identifier: Apache-2.0
"use client";

import { useState } from "react";
import { Button, ErrorNote, Field, Input, Textarea } from "@/components/ui";
import type { MemorySpace } from "@/lib/assistants-api";

/** Confirm and Reject, the two decisions on a proposal. */
export function Decide({ onConfirm, onReject }: { onConfirm: () => void; onReject: () => void }) {
  return (
    <span className="flex gap-1">
      <Button size="sm" tone="primary" onClick={onConfirm}>
        Confirm
      </Button>
      <Button size="sm" tone="default" onClick={onReject}>
        Reject
      </Button>
    </span>
  );
}

type AddBody = { action: "add"; content?: string; document?: Record<string, unknown> };

/** Adds what a person or the owners write themselves: a fact's text, or a profile document (JSON). */
export function AddToMemory({
  kind,
  askSubject = false,
  onAdd,
}: {
  kind: MemorySpace["kind"];
  /** A `user_subject` space is written under one subject's key. */
  askSubject?: boolean;
  onAdd: (body: AddBody, subject?: string) => Promise<unknown>;
}) {
  const [text, setText] = useState("");
  const [subject, setSubject] = useState("");
  const [error, setError] = useState<string | null>(null);
  async function add() {
    setError(null);
    let body: AddBody;
    if (kind === "facts") {
      body = { action: "add", content: text.trim() };
    } else {
      try {
        const parsed: unknown = JSON.parse(text);
        if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("not an object");
        body = { action: "add", document: parsed as Record<string, unknown> };
      } catch {
        setError("The profile must be a JSON object.");
        return;
      }
    }
    if (askSubject && !subject.trim()) {
      setError("Name the subject this is about.");
      return;
    }
    try {
      await onAdd(body, askSubject ? subject.trim() : undefined);
      setText("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Refused");
    }
  }
  return (
    <div className="space-y-2" data-testid="memory-add">
      {askSubject && (
        <Field label="Subject key">{(p) => <Input {...p} value={subject} onChange={(e) => setSubject(e.target.value)} />}</Field>
      )}
      <Field label={kind === "facts" ? "Add a fact" : "Add a profile (JSON)"}>
        {(p) =>
          kind === "facts" ? (
            <Input {...p} value={text} onChange={(e) => setText(e.target.value)} />
          ) : (
            <Textarea {...p} rows={4} value={text} onChange={(e) => setText(e.target.value)} />
          )
        }
      </Field>
      {error && <ErrorNote>{error}</ErrorNote>}
      <Button size="sm" tone="primary" disabled={!text.trim()} onClick={() => void add()}>
        Add
      </Button>
    </div>
  );
}
