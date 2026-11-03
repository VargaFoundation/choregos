// SPDX-License-Identifier: Apache-2.0
"use client";

import { useState } from "react";
import { Button, Input, Select } from "@/components/ui";
import {
  CONFIDENCE_SUFFIX,
  DEFAULT_DECISION_MODEL,
  DEFAULT_DECISION_TIMEOUT_SECONDS,
  type WorkflowDecisionActor,
  type WorkflowDecisionQuestion,
} from "@/components/workflows/document";
import { operations } from "@/components/workflows/operations";
import type { WorkflowOperation } from "@/lib/types";

/** What a decision may send (the contract's pattern, ADR 0121): nothing else reaches the model. */
export const DECISION_STATE = /^(title|description|labels|type|fields\.[a-z][a-z0-9_]{0,63}|outputs\.[a-z][a-z0-9_]{0,63})$/;
/** The work item's own sources, offered in one click; `fields.<name>` and `outputs.<name>` are typed. */
export const FIXED_STATE_SOURCES = ["title", "description", "labels", "type"] as const;
const QUESTION_NAME = /^[a-z][a-z0-9_]{0,40}$/;
const ROLE = /^[a-z][a-z0-9_]{1,31}$/;
const MAX_STATE = 32;
const MAX_QUESTIONS = 16;
const MAX_TIMEOUT_SECONDS = 120;

export type QuestionType = WorkflowDecisionQuestion["type"];
/** A question as the form edits it: its options or levels as one comma-separated line. */
export type QuestionRow = { name: string; type: QuestionType; instructions: string; values: string };
export type DecisionDraft = { role: string; model: string; state: string[]; questions: QuestionRow[]; timeout: string };

const QUESTION_TYPES: { value: QuestionType; label: string }[] = [
  { value: "yes_no", label: "Yes / no" },
  { value: "choice", label: "One of a list" },
  { value: "score", label: "A score (ordered levels)" },
];

const EMPTY_QUESTION: QuestionRow = { name: "", type: "yes_no", instructions: "", values: "" };

function split(values: string): string[] {
  return values
    .split(",")
    .map((v) => v.trim())
    .filter(Boolean);
}

/** The form's starting point: an existing decision actor, or a new one with the contract's defaults. */
export function draftOf(actor?: WorkflowDecisionActor): DecisionDraft {
  if (!actor) return { role: "", model: DEFAULT_DECISION_MODEL, state: ["title", "description"], questions: [{ ...EMPTY_QUESTION }], timeout: "" };
  return {
    role: actor.role,
    model: actor.model ?? DEFAULT_DECISION_MODEL,
    state: [...actor.state],
    questions: Object.entries(actor.questions).map(([name, q]) => ({
      name,
      type: q.type,
      instructions: q.instructions,
      values: q.type === "choice" ? q.options.join(", ") : q.type === "score" ? q.levels.join(", ") : "",
    })),
    timeout: actor.timeout_seconds !== undefined ? String(actor.timeout_seconds) : "",
  };
}

/** The questions as the workflow writes them. */
export function questionsOf(rows: QuestionRow[]): Record<string, WorkflowDecisionQuestion> {
  const questions: Record<string, WorkflowDecisionQuestion> = {};
  for (const row of rows) {
    const instructions = row.instructions.trim();
    questions[row.name] =
      row.type === "choice"
        ? { type: "choice", instructions, options: split(row.values) }
        : row.type === "score"
          ? { type: "score", instructions, levels: split(row.values) }
          : { type: "yes_no", instructions };
  }
  return questions;
}

/** What the contract would refuse, in words, before the engine says it (ADR 0121). Empty: the draft can be written. */
export function decisionProblems(draft: DecisionDraft): string[] {
  const problems: string[] = [];
  if (!ROLE.test(draft.role)) problems.push("The role is 2 to 32 lowercase letters, digits or `_`, starting with a letter.");
  if (!draft.model.trim()) problems.push("Name the model: a catalogue entry with the `decide` usage.");
  if (draft.state.length === 0) problems.push("Name at least one source the model reads.");
  if (draft.state.length > MAX_STATE) problems.push(`At most ${MAX_STATE} sources.`);
  if (draft.questions.length === 0) problems.push("Ask at least one question.");
  if (draft.questions.length > MAX_QUESTIONS) problems.push(`At most ${MAX_QUESTIONS} questions.`);
  const names = draft.questions.map((q) => q.name);
  draft.questions.forEach((q, i) => {
    const label = `Question ${i + 1}`;
    if (!QUESTION_NAME.test(q.name)) problems.push(`${label}: its name is lowercase letters, digits or \`_\`, starting with a letter.`);
    else if (names.indexOf(q.name) !== i) problems.push(`${label}: another question is already named \`${q.name}\`.`);
    else if (names.includes(`${q.name}${CONFIDENCE_SUFFIX}`) || (q.name.endsWith(CONFIDENCE_SUFFIX) && names.includes(q.name.slice(0, -CONFIDENCE_SUFFIX.length))))
      problems.push(`${label}: \`${q.name}\` collides with a question's probability, \`<name>${CONFIDENCE_SUFFIX}\`.`);
    if (!q.instructions.trim()) problems.push(`${label}: say what is asked.`);
    else if (q.instructions.length > 2000) problems.push(`${label}: at most 2000 characters of instructions.`);
    const values = split(q.values);
    if (q.type === "choice" && (values.length < 2 || values.length > 100))
      problems.push(`${label}: a choice offers 2 to 100 options.`);
    if (q.type === "score" && (values.length < 2 || values.length > 10)) problems.push(`${label}: a score has 2 to 10 levels.`);
    if (q.type !== "yes_no" && new Set(values).size !== values.length) problems.push(`${label}: each option is named once.`);
  });
  if (draft.timeout !== "") {
    const seconds = Number(draft.timeout);
    if (!Number.isInteger(seconds) || seconds < 1 || seconds > MAX_TIMEOUT_SECONDS)
      problems.push(`The time to answer is a whole number of seconds, 1 to ${MAX_TIMEOUT_SECONDS}.`);
  }
  return problems;
}

/** The actor a new decision step adds, in the contract's shape. */
export function decisionSpec(draft: DecisionDraft): Record<string, unknown> {
  return {
    type: "decision",
    role: draft.role,
    model: draft.model.trim(),
    state: draft.state,
    questions: questionsOf(draft.questions),
    ...(draft.timeout !== "" ? { timeout_seconds: Number(draft.timeout) } : {}),
  };
}

/**
 * The edits that turn `before` into the draft, one `set_actor` per field that changed (ADR 0112): the
 * questions as indented lines, the rest on one line. A default left as it was is not written out.
 */
export function decisionEdits(name: string, before: WorkflowDecisionActor, draft: DecisionDraft): WorkflowOperation[] {
  const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
  const edits: WorkflowOperation[] = [];
  if (draft.role !== before.role) edits.push(operations.actorField(name, "role", draft.role));
  if (draft.model.trim() !== (before.model ?? DEFAULT_DECISION_MODEL)) edits.push(operations.actorField(name, "model", draft.model.trim()));
  if (!same(draft.state, before.state)) edits.push(operations.actorField(name, "state", draft.state));
  const questions = questionsOf(draft.questions);
  if (!same(questions, before.questions)) edits.push(operations.actorField(name, "questions", questions));
  const timeout = draft.timeout === "" ? undefined : Number(draft.timeout);
  if (timeout !== before.timeout_seconds) edits.push(operations.actorField(name, "timeout_seconds", timeout ?? null));
  return edits;
}

/**
 * A decision step's fields (ADR 0121): its role, its model, what it sends, its questions and how long it
 * waits. The parent turns the draft into typed operations — a new actor, or the fields that changed.
 */
export function DecisionActorFields({
  draft,
  onChange,
}: {
  draft: DecisionDraft;
  onChange: (draft: DecisionDraft) => void;
}) {
  const [source, setSource] = useState("");
  const set = (change: Partial<DecisionDraft>) => onChange({ ...draft, ...change });
  const setQuestion = (i: number, change: Partial<QuestionRow>) =>
    set({ questions: draft.questions.map((q, j) => (j === i ? { ...q, ...change } : q)) });
  const sourceValid = DECISION_STATE.test(source.trim()) && !draft.state.includes(source.trim());
  const addSource = (value: string) => {
    set({ state: [...draft.state, value] });
    setSource("");
  };
  return (
    <div className="space-y-2" data-testid="decision-fields">
      <label className="block space-y-1">
        <span className="text-xs text-ink-muted">Role</span>
        <Input value={draft.role} onChange={(e) => set({ role: e.target.value })} />
      </label>
      <label className="block space-y-1">
        <span className="text-xs text-ink-muted">Model (a catalogue entry with the decide usage)</span>
        <Input value={draft.model} onChange={(e) => set({ model: e.target.value })} />
      </label>
      <fieldset className="space-y-1">
        <legend className="text-xs text-ink-muted">What it sends to the model, and nothing else</legend>
        <ul className="flex flex-wrap gap-1" aria-label="Sources sent to the model">
          {draft.state.map((s) => (
            <li key={s} className="inline-flex items-center gap-1 rounded border border-line px-1.5 py-0.5 font-mono text-xs">
              {s}
              <button
                type="button"
                className="text-ink-muted hover:text-danger"
                aria-label={`Remove the source ${s}`}
                onClick={() => set({ state: draft.state.filter((x) => x !== s) })}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
        <div className="flex flex-wrap gap-1">
          {FIXED_STATE_SOURCES.filter((s) => !draft.state.includes(s)).map((s) => (
            <button
              key={s}
              type="button"
              className="border border-line px-1.5 py-0.5 font-mono text-[11px] hover:border-line-strong"
              aria-label={`Send the ${s}`}
              onClick={() => addSource(s)}
            >
              + {s}
            </button>
          ))}
        </div>
        <div className="flex items-end gap-2">
          <label className="flex-1 space-y-1">
            <span className="text-xs text-ink-muted">A field or an output (fields.name, outputs.name)</span>
            <Input value={source} onChange={(e) => setSource(e.target.value)} />
          </label>
          <Button size="sm" disabled={!sourceValid} onClick={() => addSource(source.trim())}>
            Add the source
          </Button>
        </div>
        {source.trim() !== "" && !DECISION_STATE.test(source.trim()) && (
          <p className="text-xs text-warn">
            Only title, description, labels, type, fields.&lt;name&gt; or outputs.&lt;name&gt; reach a decision.
          </p>
        )}
      </fieldset>
      <fieldset className="space-y-2">
        <legend className="text-xs text-ink-muted">Questions (each writes its answer and its probability)</legend>
        {draft.questions.map((q, i) => (
          <div key={i} className="space-y-1 border-l-2 border-line pl-2" data-testid="question-row">
            <div className="grid grid-cols-[1fr_auto_auto] items-end gap-2">
              <Input aria-label={`Question ${i + 1} name`} value={q.name} onChange={(e) => setQuestion(i, { name: e.target.value })} />
              <Select aria-label={`Question ${i + 1} type`} value={q.type} onChange={(e) => setQuestion(i, { type: e.target.value as QuestionType })}>
                {QUESTION_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </Select>
              <button
                type="button"
                className="text-xs text-ink-muted underline hover:text-danger"
                aria-label={`Remove question ${q.name || i + 1}`}
                onClick={() => set({ questions: draft.questions.filter((_, j) => j !== i) })}
              >
                Remove
              </button>
            </div>
            <Input
              aria-label={`Question ${i + 1} instructions`}
              placeholder="What is asked, in plain words"
              value={q.instructions}
              onChange={(e) => setQuestion(i, { instructions: e.target.value })}
            />
            {q.type !== "yes_no" && (
              <Input
                aria-label={q.type === "choice" ? `Question ${i + 1} options (comma-separated)` : `Question ${i + 1} levels, lowest first (comma-separated)`}
                value={q.values}
                onChange={(e) => setQuestion(i, { values: e.target.value })}
              />
            )}
            {q.name && QUESTION_NAME.test(q.name) && (
              <p className="text-xs text-ink-muted">
                Writes <code>{q.name}</code> and <code>{`${q.name}${CONFIDENCE_SUFFIX}`}</code>.
              </p>
            )}
          </div>
        ))}
        <Button size="sm" disabled={draft.questions.length >= MAX_QUESTIONS} onClick={() => set({ questions: [...draft.questions, { ...EMPTY_QUESTION }] })}>
          Add a question
        </Button>
      </fieldset>
      <label className="block space-y-1">
        <span className="text-xs text-ink-muted">Answers within (seconds, {DEFAULT_DECISION_TIMEOUT_SECONDS} when empty)</span>
        <Input type="number" min={1} max={MAX_TIMEOUT_SECONDS} value={draft.timeout} onChange={(e) => set({ timeout: e.target.value })} />
      </label>
    </div>
  );
}

/** What stops the draft from being written, said under the form. */
export function DecisionProblems({ problems }: { problems: string[] }) {
  if (problems.length === 0) return null;
  return (
    <ul className="space-y-0.5 text-xs text-warn" aria-label="What the decision still needs">
      {problems.map((p) => (
        <li key={p}>{p}</li>
      ))}
    </ul>
  );
}

/** Editing a decision actor already in the workflow: only the fields that changed are written, as one gesture. */
export function EditDecisionActor({
  name,
  actor,
  apply,
  busy,
  onDone,
}: {
  name: string;
  actor: WorkflowDecisionActor;
  apply: (...ops: WorkflowOperation[]) => Promise<boolean>;
  busy: boolean;
  onDone: () => void;
}) {
  const [draft, setDraft] = useState(() => draftOf(actor));
  const problems = decisionProblems(draft);
  const edits = problems.length === 0 ? decisionEdits(name, actor, draft) : [];
  return (
    <form
      className="space-y-2 border-l-2 border-line pl-3"
      aria-label={`Edit the decision ${name}`}
      onSubmit={(event) => {
        event.preventDefault();
        void apply(...edits).then((ok) => {
          if (ok) onDone();
        });
      }}
    >
      <DecisionActorFields draft={draft} onChange={setDraft} />
      <DecisionProblems problems={problems} />
      <div className="flex items-center gap-2">
        <Button size="sm" type="submit" disabled={edits.length === 0 || busy}>
          Save the decision
        </Button>
        <button type="button" className="text-xs underline" onClick={onDone}>
          Cancel
        </button>
      </div>
    </form>
  );
}
