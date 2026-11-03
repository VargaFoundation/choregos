// SPDX-License-Identifier: Apache-2.0
/**
 * A workflow's map, laid flat (review of 07/10: "not very readable, why not lay it flat with boxes
 * of different colours"): the nominal path in one column, from top to bottom, and what leaves it
 * (retries, rejections, escalations, human waits) in a column beside it. The layout is
 * deterministic: two validations of the same YAML give the same map.
 *
 * Pure functions: the layout only depends on the graph the API returns (`dsl/graph.py`).
 */

import type { Hue } from "@/lib/workflow-hue";

export type GraphNode = {
  id: string;
  display: string;
  kind?: string;
  lane?: string;
  terminal?: boolean;
  tracker_status?: string | null;
};

export type GraphEdge = {
  id?: string;
  from: string;
  to: string;
  /** `nominal` (a transition), `reject`, `retry`, `escalate` (its exits), `default` (from any agent state). */
  kind?: string;
  label?: string;
  wildcard?: boolean;
  actor?: string | null;
  actor_type?: string | null;
  role?: string | null;
  gates?: string[];
  via?: string | null;
  timeout_hours?: number | null;
};

export type Graph = { nodes: unknown[]; edges: unknown[] };

/** Who moves the work item on from a state: its box's colour, and what it says in so many words. */
export type ActorKind = "agent" | "human" | "platform" | "train" | "decision" | "waiting" | "end";

const KINDS: Record<string, ActorKind> = {
  agent: "agent",
  human: "human",
  system: "platform",
  train: "train",
  decision: "decision",
  wait: "waiting",
  terminal: "end",
};

export function actorKind(lane: string | undefined): ActorKind {
  return KINDS[lane ?? ""] ?? "platform";
}

/** The label written on the box: colour never carries the meaning alone. */
export const KIND_LABEL: Record<ActorKind, string> = {
  agent: "agent",
  human: "human",
  platform: "platform",
  train: "release train",
  decision: "decision",
  waiting: "waiting",
  end: "end",
};

/** The categorical hue of a decision step (ADR 0120): teal, which no other kind of actor wears. */
export const DECISION_HUE: Hue = 2;

/**
 * The colours of the kinds, in the console's tokens (ADR 0043): plum for the agent, blue for the
 * person, slate for the platform, green for the train, amber for the wait, pale slate for the end.
 * The `border` draws (a box's bar, a shape's outline), the `text` writes, the `fill` tints a legend
 * dot; light and dark follow. `tests/map-layout.test.ts` measures them: a border at 3:1 at least,
 * a text at 4.5:1, on the page as on a card, in both themes.
 */
export const KIND_COLOURS: Record<ActorKind, { border: string; fill: string; text: string }> = {
  agent: { border: "var(--choregos-agent-ink)", fill: "var(--choregos-agent-soft)", text: "var(--choregos-agent-ink)" },
  human: { border: "var(--choregos-running-ink)", fill: "var(--choregos-running-soft)", text: "var(--choregos-running-ink)" },
  platform: { border: "var(--choregos-neutral-ink)", fill: "var(--choregos-neutral-soft)", text: "var(--choregos-neutral-ink)" },
  train: { border: "var(--choregos-succeeded-ink)", fill: "var(--choregos-succeeded-soft)", text: "var(--choregos-succeeded-ink)" },
  // A decision step (ADR 0121) wears the categorical teal (ADR 0120, `DECISION_HUE`): a kind of actor is an identity.
  decision: { border: "var(--choregos-cat-2-ink)", fill: "var(--choregos-cat-2-soft)", text: "var(--choregos-cat-2-ink)" },
  waiting: { border: "var(--choregos-waiting-ink)", fill: "var(--choregos-waiting-soft)", text: "var(--choregos-waiting-ink)" },
  end: { border: "var(--choregos-neutral-solid)", fill: "var(--choregos-surface-sunken)", text: "var(--choregos-neutral-ink)" },
};

const nodesOf = (graph: Graph) => graph.nodes as GraphNode[];
const edgesOf = (graph: Graph) => graph.edges as GraphEdge[];
export const isNominal = (edge: GraphEdge) => !edge.kind || edge.kind === "nominal";
const isDefault = (edge: GraphEdge) => edge.kind === "default";

/** The identifier of a transition "from any agent state": the API suffixes it with the state it leaves. */
export function transitionKey(edge: GraphEdge): string | undefined {
  if (!edge.id) return undefined;
  return edge.wildcard ? edge.id.slice(0, edge.id.lastIndexOf(":")) : edge.id;
}

/** Beyond this, the enumeration of paths stops: a heavily meshed workflow must not freeze the page. */
const MAX_VISITS = 20_000;

/**
 * The nominal path: the longest simple run of nominal transitions from the initial state
 * (otherwise the state nothing reaches, otherwise the first). At equal length, the one that ends
 * on a terminal state; then the YAML order. The transitions "from any agent state" are not part of it.
 */
export function nominalPath(graph: Graph, initial?: string | null): string[] {
  const nodes = nodesOf(graph);
  if (nodes.length === 0) return [];
  const rank = new Map(nodes.map((n, i) => [n.id, i]));
  const terminal = new Set(nodes.filter((n) => n.terminal).map((n) => n.id));
  const edges = edgesOf(graph);
  const nexts = new Map<string, string[]>();
  for (const a of edges) {
    if (!isNominal(a) || a.wildcard || !rank.has(a.to)) continue;
    const list = nexts.get(a.from) ?? [];
    if (!list.includes(a.to)) list.push(a.to);
    nexts.set(a.from, list);
  }
  // Without a declared initial state: the one nothing reaches and a transition leaves; otherwise the
  // first one a transition leaves (a cyclic workflow); otherwise the first.
  const reached = new Set(edges.filter((a) => !isDefault(a)).map((a) => a.to));
  const start =
    initial && rank.has(initial)
      ? initial
      : (nodes.find((n) => !reached.has(n.id) && nexts.has(n.id))?.id ??
        nodes.find((n) => nexts.has(n.id))?.id ??
        nodes[0]!.id);

  let best: string[] = [start];
  const betterThan = (a: string[], b: string[]) => {
    if (a.length !== b.length) return a.length > b.length;
    const ta = terminal.has(a.at(-1)!);
    const tb = terminal.has(b.at(-1)!);
    if (ta !== tb) return ta;
    for (let i = 0; i < a.length; i += 1) {
      const d = (rank.get(a[i]!) ?? 0) - (rank.get(b[i]!) ?? 0);
      if (d !== 0) return d < 0;
    }
    return false;
  };
  let visits = 0;
  const path = [start];
  const seen = new Set([start]);
  const explore = (current: string) => {
    visits += 1;
    if (visits > MAX_VISITS) return;
    if (betterThan(path, best)) best = [...path];
    for (const next of nexts.get(current) ?? []) {
      if (seen.has(next)) continue;
      seen.add(next);
      path.push(next);
      explore(next);
      path.pop();
      seen.delete(next);
    }
  };
  explore(start);
  return best;
}

export type SideState = {
  id: string;
  /** The states one falls into it from, and how: "Ready (on failure)". */
  reachedFrom: { id: string; how: string }[];
};

export type MapLayout = {
  path: string[];
  sides: SideState[];
  /** The reading order: the path, then the column beside it. ↓ and ↑ follow it. */
  order: string[];
  /** The transitions from any agent state, once each, under their own identifier. */
  wildcards: { key: string; to: string; actor?: string | null }[];
};

/** How an edge leads to a side state, in plain words. */
export function howReached(edge: GraphEdge): string {
  switch (edge.kind) {
    case "reject":
      return "if rejected";
    case "retry":
      return secondaryLabel(edge);
    case "escalate":
      return secondaryLabel(edge);
    case "default":
      return `on ${secondaryLabel(edge)}`;
    default:
      return edge.actor ? `by ${edge.actor}` : "next";
  }
}

/**
 * A secondary edge's label. The engine writes them in English (ADR 0046, S34-03;
 * `packages/core/tests/test_graph_labels.py` holds it there), so a label is passed through as it
 * comes: only the retry bound is rephrased.
 */
export function secondaryLabel(edge: GraphEdge): string {
  const raw = (edge.label ?? "").trim();
  const bound = /\(\s*≤\s*(\d+)\s*\)/.exec(raw)?.[1];
  if (/^on failure/.test(raw)) return bound ? `on failure (up to ${bound} retries)` : "on failure";
  return raw || edge.kind || "";
}

export function layoutMap(graph: Graph, initial?: string | null): MapLayout {
  const nodes = nodesOf(graph);
  const edges = edgesOf(graph);
  const path = nominalPath(graph, initial);
  const onPath = new Set(path);
  const position = new Map(path.map((id, i) => [id, i]));
  const rank = new Map(nodes.map((n, i) => [n.id, i]));

  const sides = nodes
    .filter((n) => !onPath.has(n.id))
    .map((n) => {
      const incoming = edges.filter((a) => a.to === n.id && a.from !== n.id);
      // An exit of its own (rejection, retry, escalation) anchors the state before a "from any agent state" default.
      const reachedFrom = incoming
        .filter((a) => onPath.has(a.from))
        .sort(
          (a, b) =>
            Number(isDefault(a)) - Number(isDefault(b)) || (position.get(a.from) ?? 0) - (position.get(b.from) ?? 0),
        )
        .map((a) => ({ id: a.from, how: howReached(a) }));
      // Reached only from another side state (the abandon from the human intervention): it is said too.
      const viaOther = incoming
        .filter((a) => !onPath.has(a.from))
        .map((a) => ({ id: a.from, how: howReached(a) }));
      const seen = new Set<string>();
      const unique = [...reachedFrom, ...viaOther].filter((d) => (seen.has(`${d.id}|${d.how}`) ? false : seen.add(`${d.id}|${d.how}`)));
      return { id: n.id, reachedFrom: unique };
    })
    // Lined up with the first path state one falls into them from; those reached through another
    // side state come after it; then the YAML order.
    .sort((a, b) => anchorOf(a) - anchorOf(b) || (rank.get(a.id) ?? 0) - (rank.get(b.id) ?? 0));

  function anchorOf(side: SideState): number {
    const pathEntry = side.reachedFrom.find((d) => onPath.has(d.id));
    if (pathEntry) return position.get(pathEntry.id) ?? 0;
    const viaSide = side.reachedFrom[0];
    if (viaSide) {
      const source = nodes.find((n) => n.id === viaSide.id);
      const parent = source ? edges.find((a) => a.to === source.id && onPath.has(a.from)) : undefined;
      return (parent ? (position.get(parent.from) ?? path.length) : path.length) + 0.5;
    }
    return path.length + 1;
  }

  const wildcards = new Map<string, { key: string; to: string; actor?: string | null }>();
  for (const a of edges) {
    if (!a.wildcard) continue;
    const key = transitionKey(a);
    if (key && !wildcards.has(key)) wildcards.set(key, { key, to: a.to, actor: a.actor });
  }
  return { path, sides, order: [...path, ...sides.map((c) => c.id)], wildcards: [...wildcards.values()] };
}

/** The transitions a box carries: the nominal one towards the next state of the path, and the others. */
export function exitsOf(graph: Graph, id: string, next?: string) {
  const edges = edgesOf(graph).filter((a) => a.from === id && !isDefault(a) && !a.wildcard);
  const nominal = edges.filter(isNominal);
  const main = nominal.find((a) => a.to === next) ?? (next ? undefined : nominal[0]);
  return {
    main,
    others: nominal.filter((a) => a !== main),
    secondary: edges.filter((a) => !isNominal(a)),
  };
}

/** The transition behind a choice on the map: by its identifier, or by the key of a wildcard transition. */
export function mapEdge(graph: Graph, id: string): GraphEdge | undefined {
  const edges = edgesOf(graph).filter((a) => !isDefault(a));
  const direct = edges.find((a) => a.id === id && !a.wildcard);
  if (direct) return direct;
  const wildcard = edges.find((a) => a.wildcard && transitionKey(a) === id);
  return wildcard ? { ...wildcard, id } : undefined;
}

/** What happens from any agent state, said once: `on question → Needs a human`. */
export function defaults(graph: Graph): Array<{ label: string; to: string; from: string[] }> {
  const byId = new Map(nodesOf(graph).map((n) => [n.id, n]));
  const groups = new Map<string, { label: string; to: string; from: string[] }>();
  for (const a of edgesOf(graph).filter(isDefault)) {
    const label = secondaryLabel(a) || "default";
    const key = `${label}->${a.to}`;
    const entry = groups.get(key) ?? { label, to: byId.get(a.to)?.display ?? a.to, from: [] };
    entry.from.push(byId.get(a.from)?.display ?? a.from);
    groups.set(key, entry);
  }
  return [...groups.values()];
}

/** The keyboard walk: where each key leads from a state, and how to describe it. */
export function navigation(graph: Graph, initial?: string | null) {
  const { order, path } = layoutMap(graph, initial);
  const byId = new Map(nodesOf(graph).map((n) => [n.id, n]));
  const edges = edgesOf(graph).filter((a) => !isDefault(a));

  // → follows the nominal transition (the path's first); ← goes back up. Deterministic, like the map.
  function follow(reachedFrom: string, direction: "down" | "up"): string | null {
    const i = path.indexOf(reachedFrom);
    if (i >= 0) {
      const neighbour = direction === "down" ? path[i + 1] : path[i - 1];
      if (neighbour) return neighbour;
    }
    const candidates = edges.filter((a) => (direction === "down" ? a.from : a.to) === reachedFrom);
    const choice = candidates.find(isNominal) ?? candidates[0];
    if (!choice) return null;
    return direction === "down" ? choice.to : choice.from;
  }

  function move(reachedFrom: string, key: string): string | null | undefined {
    const index = order.indexOf(reachedFrom);
    if (index < 0) return undefined;
    switch (key) {
      case "ArrowRight":
        return follow(reachedFrom, "down");
      case "ArrowLeft":
        return follow(reachedFrom, "up");
      case "ArrowDown":
        return order[Math.min(index + 1, order.length - 1)] ?? null;
      case "ArrowUp":
        return order[Math.max(index - 1, 0)] ?? null;
      case "Home":
        return order[0] ?? null;
      case "End":
        return order[order.length - 1] ?? null;
      default:
        return undefined;
    }
  }

  function describe(id: string): string {
    const node = byId.get(id);
    if (!node) return "";
    const kind = KIND_LABEL[actorKind(node.lane)];
    const head = `${node.display} (${kind}${node.terminal ? ", terminal" : ""})`;
    const outgoing = edges.filter((a) => a.from === id && !a.wildcard);
    if (outgoing.length === 0) return `${head}: no outgoing transition.`;
    const targets = outgoing.map((a) => {
      const target = byId.get(a.to)?.display ?? a.to;
      if (!isNominal(a)) return `${target} ${howReached(a)}`;
      const via = [a.actor ? `by ${a.actor}` : a.via ? `by the ${a.via.replace("_", " ")}` : null, a.gates?.length ? `gates ${a.gates.join(", ")}` : null]
        .filter(Boolean)
        .join(", ");
      return via ? `${target} (${via})` : target;
    });
    return `${head}: → ${targets.join("; → ")}.`;
  }

  return { order, move, describe };
}
