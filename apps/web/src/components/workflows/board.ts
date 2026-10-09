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
  return columns.filter((column) => column.items.length > 0 || declared.length <= 12);
}

/** Les tickets d'un workflow : ceux qui y sont épinglés ; un ticket d'avant l'épingle va au défaut. */
export function itemsOf(items: WorkItemDto[], workflow: string, isDefault: boolean): WorkItemDto[] {
  return items.filter((item) => (item.workflow_name ? item.workflow_name === workflow : isDefault));
}
