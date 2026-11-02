// SPDX-License-Identifier: Apache-2.0
import type { WorkflowGraph, WorkItemDto } from "@/lib/types";

export interface Column {
  state: string;
  display: string;
  /** Le genre que le workflow déclare ; absent, le badge le déduit du nom de l'état. */
  kind?: string;
  items: WorkItemDto[];
  /** L'état n'est pas dans ce workflow (ticket migré, ancien) : la colonne le dit (S23-04). */
  horsWorkflow?: boolean;
}

/**
 * Les colonnes d'un board : les états du workflow, dans son ordre, tels que le VALIDATEUR les lit
 * — le code de l'orchestrateur. Un regex sur le YAML les lisait avant : un YAML indenté de quatre
 * espaces, ou écrit en bloc, donnait zéro colonne (ADR 0031, S16-10). Un ticket dans un état que le
 * workflow ne connaît pas (migré, ancien) reste visible, dans une colonne à part.
 */
export function columnsFromGraph(graph: WorkflowGraph | undefined, items: WorkItemDto[]): Column[] {
  const declared = (graph?.nodes ?? []).map((node) => ({
    state: node.id,
    display: node.display,
    kind: node.terminal ? "terminal" : node.kind === "wait" ? "wait" : undefined,
  }));
  const byState = new Map<string, WorkItemDto[]>();
  for (const item of items) byState.set(item.state, [...(byState.get(item.state) ?? []), item]);
  const columns: Column[] = declared.map((entry) => ({ ...entry, items: byState.get(entry.state) ?? [] }));
  for (const [state, stateItems] of byState) {
    if (!columns.some((column) => column.state === state)) {
      columns.push({ state, display: stateItems[0]?.state_display ?? state, items: stateItems, horsWorkflow: true });
    }
  }
  return columns;
}

/**
 * The columns a board shows: those holding a ticket, and the empty ones only on request. A workflow of
 * eight states with two tickets used to need a horizontal scroll to reach them (2026-11-02). A column
 * outside the workflow always holds a ticket, so it is always shown.
 */
export function visibleColumns(columns: Column[], showEmpty: boolean): { shown: Column[]; hidden: number } {
  const shown = showEmpty ? columns : columns.filter((column) => column.items.length > 0);
  return { shown, hidden: columns.length - shown.length };
}

/** How many tickets a terminal column lists before it folds the rest behind "Show all". */
export const TERMINAL_FOLD = 3;

/**
 * The tickets a column lists: all of them, except in a terminal column, which keeps its most recent
 * ones and folds the rest — a "Done" column grows forever and pushed the live ones out of sight.
 */
export function foldedItems(column: Column, expanded: boolean): { items: WorkItemDto[]; folded: number } {
  if (column.kind !== "terminal" || expanded || column.items.length <= TERMINAL_FOLD) {
    return { items: column.items, folded: 0 };
  }
  const recent = [...column.items].sort((a, b) => recency(b) - recency(a)).slice(0, TERMINAL_FOLD);
  return { items: recent, folded: column.items.length - recent.length };
}

function recency(item: WorkItemDto): number {
  const when = item.closed_at ?? item.created_at;
  return when ? Date.parse(when) : 0;
}

/** The workflows a board shows: all of them, the default first, or the one `?workflow=` names. */
export function boardWorkflows<T extends { name: string; is_default: boolean }>(
  workflows: T[],
  chosen: string | null,
): T[] {
  if (chosen) return workflows.filter((workflow) => workflow.name === chosen);
  return [...workflows].sort((a, b) => Number(b.is_default) - Number(a.is_default));
}

/** Les tickets d'un workflow : ceux qui y sont épinglés ; un ticket d'avant l'épingle va au défaut. */
export function itemsOf(items: WorkItemDto[], workflow: string, isDefault: boolean): WorkItemDto[] {
  return items.filter((item) => (item.workflow_name ? item.workflow_name === workflow : isDefault));
}
