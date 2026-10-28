// SPDX-License-Identifier: Apache-2.0
"use client";

import { useState, type ReactNode } from "react";
import { Button, Input, Select, Textarea } from "@/components/ui";
import { useBrouillon } from "@/components/workflows/brouillon";
import { transition as transitionOf, type ReadTransition } from "@/components/workflows/document";
import { FieldsEditor } from "@/components/workflows/fields-editor";
import { rowsToSchema, schemaToRows, withFields } from "@/components/workflows/fields-schema";
import { operations } from "@/components/workflows/operations";
import type { WorkflowOperation } from "@/lib/types";

/** `a, b` → ["a", "b"]: names, without blanks or repeats. */
export function names(text: string): string[] {
  return [...new Set(text.split(",").map((n) => n.trim()).filter(Boolean))];
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <details className="rounded border border-line px-3 py-2">
      <summary className="cursor-pointer text-sm">{title}</summary>
      <div className="mt-2 space-y-2">{children}</div>
    </details>
  );
}

function Labelled({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block space-y-1">
      <span className="text-xs text-ink-muted">{label}</span>
      {children}
    </label>
  );
}

/**
 * Every field of a transition the map did not edit yet (ADR 0109 §3), each saved as one typed operation
 * on the draft; a nested value — a task, an action, a fan-out — is written as indented lines, so the
 * diff a reviewer reads is one field per line. The validator still decides: a refusal shows in the draft bar.
 */
export function TransitionFields({ id, states, actors, workflows }: { id: string; states: string[]; actors: string[]; workflows: string[] }) {
  const draft = useBrouillon();
  const read = transitionOf(draft.document, id);
  if (!read) return null;
  // Re-seeded when the draft changes underneath (an undo, another gesture).
  return <TransitionFieldsForm key={JSON.stringify(read)} id={id} read={read} states={states} actors={actors} workflows={workflows} />;
}

function TransitionFieldsForm({
  id,
  read,
  states,
  actors,
  workflows,
}: {
  id: string;
  read: ReadTransition;
  states: string[];
  actors: string[];
  workflows: string[];
}) {
  const draft = useBrouillon();
  /** One operation per field — none to clear a field the transition does not have (the engine refuses it). */
  const field = (name: string, value: unknown): WorkflowOperation[] => {
    const empty = value === null || value === undefined || value === "" || (Array.isArray(value) && value.length === 0);
    return empty && (read as Record<string, unknown>)[name] === undefined ? [] : [operations.transitionField(id, name, value)];
  };
  const apply = (...ops: WorkflowOperation[]) => {
    if (ops.length > 0) void draft.appliquer(...ops);
  };
  const set = (name: string, value: unknown) => apply(...field(name, value));

  const [inputs, setInputs] = useState((read.inputs ?? []).join(", "));
  const [outputs, setOutputs] = useState((read.outputs ?? []).join(", "));
  const [failTo, setFailTo] = useState(read.on_fail?.to ?? "");
  const [attempts, setAttempts] = useState(String(read.on_fail?.max_attempts ?? 2));
  const [escalate, setEscalate] = useState(read.on_fail?.escalate_to ?? "");
  const [onReject, setOnReject] = useState(read.on_reject ?? "");
  const [does, setDoes] = useState(read.does ?? "");
  const [train, setTrain] = useState(read.via === "release_train");
  const [trainEnv, setTrainEnv] = useState(read.train?.env ?? "prod");
  const [trainApproval, setTrainApproval] = useState(read.train?.approval ?? "");
  const [taskTitle, setTaskTitle] = useState(read.task?.title ?? "");
  const [taskInstructions, setTaskInstructions] = useState(read.task?.instructions ?? "");
  const [taskAttest, setTaskAttest] = useState(read.task?.attest ?? "");
  const [taskRows, setTaskRows] = useState(schemaToRows(read.task?.form));
  const [actionKind, setActionKind] = useState(read.action?.kind ?? "");
  const [actionTitle, setActionTitle] = useState(read.action?.title ?? "");
  const [actionWhy, setActionWhy] = useState(read.action?.justification ?? "");
  const [actionEffects, setActionEffects] = useState(read.action?.effects ? JSON.stringify(read.action.effects, null, 2) : "");
  const spawn = (read.spawn ?? {}) as { workflow?: string; for_each?: string; title?: string; max_children?: number; min_succeeded?: number };
  const [spawnWorkflow, setSpawnWorkflow] = useState(spawn.workflow ?? "");
  const [spawnEach, setSpawnEach] = useState(spawn.for_each ?? "");
  const [spawnTitle, setSpawnTitle] = useState(spawn.title ?? "");
  const [spawnMax, setSpawnMax] = useState(String(spawn.max_children ?? 20));

  const stateOptions = (
    <>
      <option value="">—</option>
      {states.map((s) => (
        <option key={s} value={s}>
          {s}
        </option>
      ))}
    </>
  );
  let effects: unknown[] | null = null;
  let effectsError: string | null = null;
  if (actionEffects.trim()) {
    try {
      const parsed: unknown = JSON.parse(actionEffects);
      if (Array.isArray(parsed)) effects = parsed;
      else effectsError = "a list is expected: `[{\"effect\": …}]`";
    } catch (cause) {
      effectsError = cause instanceof Error ? cause.message : "not JSON";
    }
  }

  return (
    <div className="space-y-2" data-testid="transition-fields">
      <Section title="Inputs and outputs">
        <Labelled label="Reads (names, comma-separated)">
          <Input value={inputs} onChange={(e) => setInputs(e.target.value)} />
        </Labelled>
        <Labelled label="Writes (names, comma-separated)">
          <Input value={outputs} onChange={(e) => setOutputs(e.target.value)} />
        </Labelled>
        <Button size="sm" onClick={() => apply(...field("inputs", names(inputs)), ...field("outputs", names(outputs)))}>
          Save inputs and outputs
        </Button>
      </Section>

      <Section title="When it fails or is refused">
        <div className="grid grid-cols-3 gap-2">
          <Labelled label="Back to">
            <Select value={failTo} onChange={(e) => setFailTo(e.target.value)}>
              {stateOptions}
            </Select>
          </Labelled>
          <Labelled label="Attempts">
            <Input type="number" min={1} value={attempts} onChange={(e) => setAttempts(e.target.value)} />
          </Labelled>
          <Labelled label="Then to">
            <Select value={escalate} onChange={(e) => setEscalate(e.target.value)}>
              {stateOptions}
            </Select>
          </Labelled>
        </div>
        <Button
          size="sm"
          onClick={() =>
            set(
              "on_fail",
              failTo ? { to: failTo, max_attempts: Number(attempts) || 1, ...(escalate ? { escalate_to: escalate } : {}) } : null,
            )
          }
        >
          Save the failure path
        </Button>
        <Labelled label="A person's refusal goes to">
          <Select value={onReject} onChange={(e) => setOnReject(e.target.value)}>
            {stateOptions}
          </Select>
        </Labelled>
        <Button size="sm" onClick={() => set("on_reject", onReject || null)}>
          Save the refusal path
        </Button>
      </Section>

      <Section title="What the platform does">
        <Labelled label="Effect of a platform step">
          <Select value={does} onChange={(e) => setDoes(e.target.value)}>
            <option value="">Nothing</option>
            <option value="open_pr">Opens a pull request</option>
            <option value="merge_pr">Merges the pull request</option>
          </Select>
        </Labelled>
        <Button size="sm" onClick={() => set("does", does || null)}>
          Save the effect
        </Button>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={train} onChange={(e) => setTrain(e.target.checked)} />
          Goes through a release train
        </label>
        {train && (
          <div className="grid grid-cols-2 gap-2">
            <Labelled label="Environment">
              <Input value={trainEnv} onChange={(e) => setTrainEnv(e.target.value)} />
            </Labelled>
            <Labelled label="Approved by">
              <Select value={trainApproval} onChange={(e) => setTrainApproval(e.target.value)}>
                <option value="">—</option>
                {actors.map((a) => (
                  <option key={a} value={a}>
                    {a}
                  </option>
                ))}
              </Select>
            </Labelled>
          </div>
        )}
        <Button
          size="sm"
          onClick={() =>
            train
              ? apply(...field("via", "release_train"), ...field("train", { env: trainEnv, ...(trainApproval ? { approval: trainApproval } : {}) }))
              : apply(...field("via", null), ...field("train", null))
          }
        >
          Save the release train
        </Button>
      </Section>

      <Section title="A task a person does">
        <Labelled label="Title">
          <Input value={taskTitle} onChange={(e) => setTaskTitle(e.target.value)} />
        </Labelled>
        <Labelled label="Instructions">
          <Textarea rows={3} value={taskInstructions} onChange={(e) => setTaskInstructions(e.target.value)} />
        </Labelled>
        <FieldsEditor label="What they fill in (fields of the work item)" rows={taskRows} onChange={setTaskRows} />
        <Labelled label="What they attest">
          <Input value={taskAttest} onChange={(e) => setTaskAttest(e.target.value)} />
        </Labelled>
        <Button
          size="sm"
          onClick={() => {
            const form = rowsToSchema(taskRows);
            if (!form) return set("task", null);
            const task = {
              ...(taskTitle ? { title: taskTitle } : {}),
              ...(taskInstructions ? { instructions: taskInstructions } : {}),
              form,
              ...(taskAttest ? { attest: taskAttest } : {}),
            };
            // A form may only fill declared fields: the ones it adds are declared in the same gesture.
            apply(operations.workflowField("inputs", withFields(draft.document?.metadata?.inputs, form)), ...field("task", task));
          }}
        >
          Save the task
        </Button>
      </Section>

      <Section title="A governed action">
        <div className="grid grid-cols-2 gap-2">
          <Labelled label="Kind">
            <Input value={actionKind} onChange={(e) => setActionKind(e.target.value)} placeholder="account.create" />
          </Labelled>
          <Labelled label="Title">
            <Input value={actionTitle} onChange={(e) => setActionTitle(e.target.value)} />
          </Labelled>
        </div>
        <Labelled label="Justification">
          <Input value={actionWhy} onChange={(e) => setActionWhy(e.target.value)} />
        </Labelled>
        <Labelled label="Effects (JSON list)">
          <Textarea className="font-mono text-xs" rows={4} value={actionEffects} onChange={(e) => setActionEffects(e.target.value)} />
        </Labelled>
        {effectsError && <p className="text-xs text-warn">{effectsError}</p>}
        <Button
          size="sm"
          disabled={!!effectsError}
          onClick={() =>
            set(
              "action",
              actionKind
                ? { ...(read.action ?? {}), kind: actionKind, title: actionTitle, ...(actionWhy ? { justification: actionWhy } : {}), effects: effects ?? [] }
                : null,
            )
          }
        >
          Save the action
        </Button>
      </Section>

      <Section title="A child work item per element (fan-out)">
        <div className="grid grid-cols-2 gap-2">
          <Labelled label="Workflow of each child">
            <Select value={spawnWorkflow} onChange={(e) => setSpawnWorkflow(e.target.value)}>
              <option value="">—</option>
              {workflows.map((w) => (
                <option key={w} value={w}>
                  {w}
                </option>
              ))}
            </Select>
          </Labelled>
          <Labelled label="For each element of">
            <Input value={spawnEach} onChange={(e) => setSpawnEach(e.target.value)} placeholder="fields.symbols" />
          </Labelled>
          <Labelled label="Title of a child">
            <Input value={spawnTitle} onChange={(e) => setSpawnTitle(e.target.value)} />
          </Labelled>
          <Labelled label="At most">
            <Input type="number" min={1} max={100} value={spawnMax} onChange={(e) => setSpawnMax(e.target.value)} />
          </Labelled>
        </div>
        <Button
          size="sm"
          onClick={() =>
            set(
              "spawn",
              spawnWorkflow
                ? { ...(read.spawn ?? {}), workflow: spawnWorkflow, for_each: spawnEach, title: spawnTitle, max_children: Number(spawnMax) || 20 }
                : null,
            )
          }
        >
          Save the fan-out
        </Button>
      </Section>
    </div>
  );
}
