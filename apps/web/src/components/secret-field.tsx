// SPDX-License-Identifier: Apache-2.0
"use client";

import { useState } from "react";
import { Button, ErrorNote, Input } from "@/components/ui";
import { relative } from "@/lib/format";
import type { ConnectorSecretState } from "@/lib/types";

/** A secret reference, not a value: `scheme:rest` (`env:JIRA_TOKEN`). */
export function estUneReference(valeur: string): boolean {
  return /^[a-z][a-z0-9-]{1,31}:.+$/.test(valeur);
}

/**
 * What a person typed for one secret field: a VALUE (sealed by the platform, never shown again) or a
 * REFERENCE (`env:NAME`, for an operator who prefers a vault). ADR 0058.
 */
export type SecretDraft = { mode: "value" | "reference"; text: string };

export const EMPTY_DRAFT: SecretDraft = { mode: "value", text: "" };

/** Why a draft cannot be sent, or null: a reference must look like one, a value must not be empty. */
export function problemOfDraft(draft: SecretDraft): string | null {
  if (!draft.text) return null;
  if (draft.mode === "reference" && !estUneReference(draft.text)) {
    return "A reference is expected (env:NAME), not a value";
  }
  return null;
}

/** The line under a configured secret: where it comes from, and when it last changed. Never the value. */
export function stateLine(state: ConnectorSecretState): string {
  if (state.source === "reference") return `reference ${state.reference ?? ""}`.trim();
  if (state.version == null) return "stored value missing: type it again";
  const by = state.rotated_by ? ` by ${state.rotated_by}` : "";
  return `set · rotated ${relative(state.rotated_at)}${by}`;
}

function DraftInput({
  field,
  draft,
  onChange,
  allowReference,
  onEnter,
}: {
  field: string;
  draft: SecretDraft;
  onChange: (draft: SecretDraft) => void;
  allowReference: boolean;
  onEnter?: () => void;
}) {
  const label = field.replaceAll("_", " ");
  const problem = problemOfDraft(draft);
  return (
    <div className="space-y-1">
      <Input
        className="w-full font-mono"
        // A value is a password: no autofill of the person's own login, no spellcheck, never echoed.
        type={draft.mode === "value" ? "password" : "text"}
        autoComplete="new-password"
        spellCheck={false}
        aria-label={draft.mode === "value" ? `${label} value` : `${label} reference`}
        placeholder={draft.mode === "value" ? "type the value once" : "env:NAME"}
        value={draft.text}
        onChange={(event) => onChange({ ...draft, text: event.target.value })}
        onKeyDown={(event) => {
          // Enter saves THIS secret, not a form that happens to surround the field.
          if (event.key === "Enter" && onEnter) {
            event.preventDefault();
            onEnter();
          }
        }}
      />
      <p className="text-xs text-ink-muted">
        {draft.mode === "value" ? "Stored encrypted; it is never shown again." : "The platform reads it when the connector is used."}{" "}
        {allowReference && (
          <button
            type="button"
            className="underline"
            onClick={() => onChange({ mode: draft.mode === "value" ? "reference" : "value", text: "" })}
          >
            {draft.mode === "value" ? "Use a reference instead" : "Type the value instead"}
          </button>
        )}
      </p>
      {problem && (
        <p className="text-xs text-danger" role="alert">
          {problem}
        </p>
      )}
    </div>
  );
}

/**
 * One secret field of a connector (ADR 0058).
 *
 * - Without `state` (declaring): an input whose draft goes up through `onChange`.
 * - With `onSave` (a field of an existing connector): the line "set · rotated 3 days ago by x" (or "not
 *   set"), then Replace (an empty input, never prefilled: the value is not known to the console) and
 *   Clear. `onSave` and `onClear` do the call; the field shows their error.
 */
export function SecretField({
  field,
  state,
  draft,
  onChange,
  onSave,
  onClear,
  allowReference = true,
}: {
  field: string;
  state?: ConnectorSecretState | undefined;
  draft?: SecretDraft;
  onChange?: (draft: SecretDraft) => void;
  onSave?: (draft: SecretDraft) => Promise<void>;
  onClear?: () => Promise<void>;
  allowReference?: boolean;
}) {
  const label = field.replaceAll("_", " ");
  const [editing, setEditing] = useState(false);
  const [own, setOwn] = useState<SecretDraft>(EMPTY_DRAFT);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (!state && !onSave) {
    return (
      <div className="space-y-1" data-testid={`secret-${field}`}>
        <span className="text-xs text-ink-muted">{label}</span>
        <DraftInput
          field={field}
          draft={draft ?? EMPTY_DRAFT}
          onChange={(next) => onChange?.(next)}
          allowReference={allowReference}
        />
      </div>
    );
  }

  async function run(action: () => Promise<void>, done?: () => void) {
    setError(null);
    setBusy(true);
    try {
      await action();
      done?.();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "refused");
    } finally {
      setBusy(false);
    }
  }

  const submit = () =>
    run(
      () => onSave!(own),
      () => {
        setEditing(false);
        setOwn(EMPTY_DRAFT);
      },
    );

  return (
    <div className="space-y-1" data-testid={`secret-${field}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="w-40 text-xs text-ink-muted">{label}</span>
        <span className="text-sm" data-testid={`secret-state-${field}`}>
          {state ? stateLine(state) : "not set"}
        </span>
        {!editing && (
          <span className="ml-auto inline-flex gap-2">
            {onSave && (
              <Button size="sm" onClick={() => setEditing(true)}>
                {state ? "Replace" : "Set"}
              </Button>
            )}
            {onClear && state && (
              <Button size="sm" disabled={busy} onClick={() => void run(onClear)}>
                Clear
              </Button>
            )}
          </span>
        )}
      </div>
      {editing && onSave && (
        // A div, not a form: this field also lives inside the project connector's own form, and
        // forms do not nest. Enter saves the secret, not the surrounding form.
        <div className="space-y-2">
          <DraftInput
            field={field}
            draft={own}
            onChange={setOwn}
            allowReference={allowReference}
            onEnter={() => {
              if (own.text && problemOfDraft(own) === null) void submit();
            }}
          />
          <div className="flex gap-2">
            <Button
              size="sm"
              tone="primary"
              disabled={busy || !own.text || problemOfDraft(own) !== null}
              onClick={() => void submit()}
            >
              Save
            </Button>
            <Button
              size="sm"
              onClick={() => {
                setEditing(false);
                setOwn(EMPTY_DRAFT);
                setError(null);
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}
      {error && <ErrorNote>{error}</ErrorNote>}
    </div>
  );
}

/** The body fields a set of drafts turn into: values to seal, references to keep. */
export function bodyOfDrafts(drafts: Record<string, SecretDraft>): {
  secret_values: Record<string, string>;
  secret_refs: Record<string, string>;
} {
  const secret_values: Record<string, string> = {};
  const secret_refs: Record<string, string> = {};
  for (const [field, draft] of Object.entries(drafts)) {
    if (!draft.text) continue;
    (draft.mode === "value" ? secret_values : secret_refs)[field] = draft.text;
  }
  return { secret_values, secret_refs };
}

/** `api_key.3` -> ["api_key", 3]; `api_key` -> ["api_key", 1] (ADR 0089). */
function slotOf(name: string): [string, number] {
  const m = /^(.+)\.(\d+)$/.exec(name);
  return m ? [m[1] ?? name, Number(m[2])] : [name, 1];
}

/** Secret field names in slot order: `api_key`, `api_key.2` … `api_key.10`, then the next field. */
export function inSlotOrder(names: string[]): string[] {
  return [...new Set(names)].sort((a, b) => {
    const [fa, na] = slotOf(a);
    const [fb, nb] = slotOf(b);
    return fa === fb ? na - nb : fa < fb ? -1 : 1;
  });
}

/** The first free slot of a pooled field among `taken`, or null when all ten are used (ADR 0089). */
export function nextSlot(field: string, taken: string[]): string | null {
  if (!taken.includes(field)) return field;
  for (let n = 2; n <= 10; n += 1) {
    if (!taken.includes(`${field}.${n}`)) return `${field}.${n}`;
  }
  return null;
}

/**
 * "Add a key" under a pooled field (ADR 0089): the connector uses its keys in turn and moves to the
 * next when one is rate limited or refused. A key is replaced without a gap: add the new one, then
 * clear the old.
 */
export function AddKey({ field, taken, onAdd }: { field: string; taken: string[]; onAdd: (slot: string) => void }) {
  const slot = nextSlot(field, taken);
  if (slot === null) return <p className="text-xs text-ink-muted">{field.replaceAll("_", " ")}: ten keys, the most a field holds</p>;
  return (
    <Button size="sm" onClick={() => onAdd(slot)} aria-label={`Add a key to ${field.replaceAll("_", " ")}`}>
      Add a key
    </Button>
  );
}
