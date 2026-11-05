// SPDX-License-Identifier: Apache-2.0
import { api } from "@/lib/api";
import type { WorkflowRouting } from "@/lib/types";

/**
 * A template's YAML, renamed: `metadata.name` is the workflow's name in the project, and the API
 * refuses a YAML whose name is not the route's (ADR 0031). The rest of the text does not move.
 */
export function rename(yaml: string, name: string): string {
  return yaml.replace(/(metadata:\s*\{?\s*name:\s*)[^,\s}]+/, `$1${name}`);
}

/**
 * The routing with `from` replaced by `to` wherever it names it: the default, a rule's target, a hint.
 * `null` when it never names `from`, so nothing needs to be saved.
 */
export function routingRenamed(routing: WorkflowRouting, from: string, to: string): WorkflowRouting | null {
  const hints = routing.hints ?? {};
  const named =
    routing.default === from || routing.rules.some((rule) => rule.workflow === from) || from in hints;
  if (!named) return null;
  const { [from]: hint, ...others } = hints;
  return {
    ...routing,
    default: routing.default === from ? to : routing.default,
    rules: routing.rules.map((rule) => (rule.workflow === from ? { ...rule, workflow: to } : rule)),
    ...(routing.hints ? { hints: hint === undefined ? others : { ...others, [to]: hint } } : {}),
  };
}

/**
 * A workflow moved to another name (S55-18), with the API's existing gestures: its active version is
 * published under `to` (version 1, its YAML renamed), the routing that named `from` names `to`, and
 * `from` is deactivated. Its open tickets finish on the version they are pinned to; its history stays
 * under the old name. The way out for a workflow whose name a console page took (`library`, `new`,
 * `catalogue`: S55-15), which its own page can no longer show.
 */
export async function moveWorkflow(slug: string, from: string, to: string): Promise<void> {
  const current = await api.workflowNamed(slug, from);
  await api.createWorkflow(slug, to, rename(current.yaml, to));
  const moved = routingRenamed(await api.workflowRouting(slug), from, to);
  if (moved) await api.putWorkflowRouting(slug, moved);
  await api.deactivateWorkflow(slug, from);
}
