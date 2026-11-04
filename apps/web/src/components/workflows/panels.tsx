// SPDX-License-Identifier: Apache-2.0
"use client";

import { useState } from "react";
import { Button, Card, Input, Select } from "@/components/ui";
import { useDraft } from "@/components/workflows/draft";
import { COMMON_GATES, operations } from "@/components/workflows/operations";
import { TransitionFields } from "@/components/workflows/transition-fields";

type Node = { id: string; display: string; terminal?: boolean; kind?: string };
type Edge = {
  id?: string;
  from: string;
  to: string;
  kind?: string;
  actor?: string | null;
  gates?: string[];
  timeout_hours?: number | null;
};

/**
 * The "a new state" target: the state and the transition leading to it go together, and are undone
 * together. Parentheses enter no state name: the value cannot mask one.
 */
const NEW_STATE = "(new)";

/** A map state: its label, its name, its kind; removing it; leaving it towards another. */
export function StatePanel({ node, states, actors }: { node: Node; states: Node[]; actors: string[] }) {
  const draft = useDraft();
  const [label, setLabel] = useState(node.display);
  const [name, setName] = useState(node.id);
  return (
    <Card title={`State ${node.id}`}>
      <div className="space-y-3 text-sm" data-testid="state-panel">
        <form
          className="flex items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void draft.apply(operations.stateLabel(node.id, label));
          }}
        >
          <label className="flex-1 space-y-1">
            <span className="text-xs text-ink-muted">Label</span>
            <Input value={label} onChange={(event) => setLabel(event.target.value)} />
          </label>
          <Button size="sm" type="submit" disabled={!label || label === node.display}>
            Set label
          </Button>
        </form>
        <form
          className="flex items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void draft.apply(operations.renameState(node.id, name));
          }}
        >
          <label className="flex-1 space-y-1">
            <span className="text-xs text-ink-muted">Name (every reference follows)</span>
            <Input value={name} onChange={(event) => setName(event.target.value)} />
          </label>
          <Button size="sm" type="submit" disabled={!name || name === node.id}>
            Rename
          </Button>
        </form>
        {!node.terminal && <StepForm from={node.id} states={states} actors={actors} />}
        <Button size="sm" tone="danger" onClick={() => void draft.apply(operations.removeState(node.id))}>
          Remove this state
        </Button>
      </div>
    </Card>
  );
}

/**
 * One more step: from a state, to an existing or new state, carried by an actor. The state and the
 * transition go together and are undone together. Without `from`, one chooses where to start from.
 */
export function StepForm({
  from,
  states,
  actors,
  initialTarget,
  onDone,
}: {
  from?: string;
  states: Node[];
  actors: string[];
  /** The state a drawn arrow points at (ADR 0112 §4). */
  initialTarget?: string;
  onDone?: () => void;
}) {
  const draft = useDraft();
  const [picked, setPicked] = useState(from ?? "");
  const [to, setTo] = useState(initialTarget ?? "");
  const [newName, setNewName] = useState("");
  const [by, setBy] = useState(actors[0] ?? "");
  const origin = from ?? picked;
  const target = to === NEW_STATE ? newName.trim() : to;
  return (
    <form
      className="flex flex-wrap items-end gap-2"
      aria-label={from ? `New transition from ${from}` : "New step"}
      onSubmit={(event) => {
        event.preventDefault();
        const transition = operations.addTransition(origin, target, by);
        const gestures = to === NEW_STATE ? [operations.addState(target, target), transition] : [transition];
        void draft.apply(...gestures).then((ok) => {
          if (!ok) return;
          setTo("");
          setNewName("");
          onDone?.();
        });
      }}
    >
      {!from && (
        <label className="flex-1 space-y-1">
          <span className="text-xs text-ink-muted">From</span>
          <Select value={picked} onChange={(event) => setPicked(event.target.value)}>
            <option value="">—</option>
            {states
              .filter((state) => !state.terminal)
              .map((state) => (
                <option key={state.id} value={state.id}>
                  {state.display}
                </option>
              ))}
          </Select>
        </label>
      )}
      <label className="flex-1 space-y-1">
        <span className="text-xs text-ink-muted">{from ? "New transition to" : "To"}</span>
        <Select value={to} onChange={(event) => setTo(event.target.value)}>
          <option value="">—</option>
          {states
            .filter((state) => state.id !== origin)
            .map((state) => (
              <option key={state.id} value={state.id}>
                {state.display}
              </option>
            ))}
          <option value={NEW_STATE}>A new state…</option>
        </Select>
      </label>
      {to === NEW_STATE && (
        <label className="flex-1 space-y-1">
          <span className="text-xs text-ink-muted">Name of the new state</span>
          <Input value={newName} onChange={(event) => setNewName(event.target.value)} />
        </label>
      )}
      <label className="flex-1 space-y-1">
        <span className="text-xs text-ink-muted">By</span>
        <Select value={by} onChange={(event) => setBy(event.target.value)}>
          {actors.map((actor) => (
            <option key={actor} value={actor}>
              {actor}
            </option>
          ))}
        </Select>
      </label>
      <Button size="sm" type="submit" disabled={!origin || !target || !by || draft.busy}>
        Add
      </Button>
    </form>
  );
}

/** What a guarantee holds the step to, from the process view: its sentence and its thresholds. */
export type GateWords = { name?: string; summary?: string; detail?: string | null };

/** An actor in the "who moves it" list: its id, and what it is ("person · product-owners"). */
export function actorLabel(id: string, definition?: { type?: string; group?: string; role?: string; agent?: string }): string {
  if (!definition?.type) return id;
  if (definition.type === "human") return `${id} — person${definition.group ? ` · ${definition.group}` : ""}`;
  if (definition.type === "agent")
    return `${id} — ${definition.agent ? `agent ${definition.agent}` : "built-in agent"}${definition.role ? ` · ${definition.role}` : ""}`;
  if (definition.type === "job") return `${id} — job${definition.role ? ` · ${definition.role}` : ""}`;
  if (definition.type === "system") return `${id} — the platform`;
  if (definition.type === "decision") return `${id} — decision${definition.role ? ` · ${definition.role}` : ""}`;
  return `${id} — ${definition.type}`;
}

/** A transition: who moves it, its guarantees, its time limit, every other field (ADR 0112); removing it. */
export function TransitionPanel({
  edge,
  actors,
  definitions,
  gates,
  timing,
  states = [],
  workflows = [],
}: {
  edge: Edge;
  actors: string[];
  /** The actors as the definition writes them, so the list says what each one is. */
  definitions?: Record<string, { type?: string; group?: string; role?: string; agent?: string }>;
  /** The guarantees in words, by name (the process view's), when the step is known. */
  gates?: GateWords[];
  /** How long the step waits and what happens then (`timingSentence`). */
  timing?: string | null;
  /** The workflow's states and the project's workflows, for the transition's other fields (ADR 0112). */
  states?: string[];
  workflows?: string[];
}) {
  const gatesByName = new Map((gates ?? []).map((g) => [g.name, g]));
  const draft = useDraft();
  const [by, setBy] = useState(edge.actor ?? "");
  const [gate, setGate] = useState("");
  const [hours, setHours] = useState(edge.timeout_hours ? String(edge.timeout_hours) : "");
  if (!edge.id || (edge.kind && edge.kind !== "nominal")) {
    return (
      <Card title="Transition">
        <p className="text-sm text-ink-muted">This arrow is not a transition with an id: edit it in the YAML.</p>
      </Card>
    );
  }
  const id = edge.id;
  return (
    <Card title={`Transition ${id}`}>
      <div className="space-y-3 text-sm" data-testid="transition-panel">
        <form
          className="flex items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void draft.apply(operations.transitionActor(id, by));
          }}
        >
          <label className="flex-1 space-y-1">
            <span className="text-xs text-ink-muted">Who moves it</span>
            <Select value={by} onChange={(event) => setBy(event.target.value)}>
              {actors.map((actor) => (
                <option key={actor} value={actor}>
                  {actorLabel(actor, definitions?.[actor])}
                </option>
              ))}
            </Select>
          </label>
          <Button size="sm" type="submit" disabled={!by || by === edge.actor}>
            Set
          </Button>
        </form>
        <div className="space-y-1">
          <p className="text-xs text-ink-muted">Only if</p>
          <ul className="space-y-1.5" data-testid="transition-guarantees">
            {(edge.gates ?? []).map((name) => (
              <li key={name} className="flex items-start gap-2 rounded border border-line px-2 py-1 text-xs">
                <span className="min-w-0 flex-1">
                  <code>{name}</code>
                  {gatesByName.get(name)?.summary && gatesByName.get(name)?.summary !== name && (
                    <span className="text-ink"> — {gatesByName.get(name)?.summary}</span>
                  )}
                  {gatesByName.get(name)?.detail && <span className="block text-ink-muted">{gatesByName.get(name)?.detail}</span>}
                </span>
                <button
                  type="button"
                  aria-label={`Remove the guarantee ${name}`}
                  className="text-ink-muted hover:text-danger"
                  onClick={() => void draft.apply(operations.removeGate(id, name))}
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
          <form
            className="flex items-end gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              void draft.apply(operations.addGate(id, gate)).then((ok) => ok && setGate(""));
            }}
          >
            <Input
              aria-label="Guarantee to add"
              list="garanties-courantes"
              value={gate}
              onChange={(event) => setGate(event.target.value)}
            />
            <datalist id="garanties-courantes">
              {COMMON_GATES.map((name) => (
                <option key={name} value={name} />
              ))}
            </datalist>
            <Button size="sm" type="submit" disabled={!gate}>
              Add
            </Button>
          </form>
        </div>
        <form
          className="flex items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void draft.apply(operations.transitionTimeout(id, hours === "" ? null : Number(hours)));
          }}
        >
          <label className="flex-1 space-y-1">
            <span className="text-xs text-ink-muted">At most (hours, empty: the default)</span>
            <Input type="number" min={1} value={hours} onChange={(event) => setHours(event.target.value)} />
          </label>
          <Button size="sm" type="submit">
            Set
          </Button>
        </form>
        {timing && (
          <p className="text-xs text-ink-muted" data-testid="plain-delay">
            {timing}
          </p>
        )}
        <TransitionFields id={id} states={states} actors={actors} workflows={workflows} />
        <Button size="sm" tone="danger" onClick={() => void draft.apply(operations.removeTransition(id))}>
          Remove this transition
        </Button>
      </div>
    </Card>
  );
}
