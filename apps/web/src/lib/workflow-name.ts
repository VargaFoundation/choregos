// SPDX-License-Identifier: Apache-2.0
/**
 * What a workflow may be named in a project (S55-15). Its page is `/p/<slug>/workflows/<name>`, and the
 * console's own pages under `/workflows/` win over it: a workflow named `library` or `new` could never
 * be opened, and `catalogue` redirects to the library (S55-04). The API refuses those names too
 * (`services/definitions.py`, `RESERVED_WORKFLOW_NAMES`); the forms say so before it is asked.
 */
export const WORKFLOW_NAME_PATTERN = /^[a-z][a-z0-9-]{0,62}$/;

export const RESERVED_WORKFLOW_NAMES: readonly string[] = ["library", "new", "catalogue"];

/** Why a name cannot be given to a new workflow, or `undefined` when it can. */
export function workflowNameError(name: string): string | undefined {
  if (!WORKFLOW_NAME_PATTERN.test(name)) return "Lowercase letters, digits and dashes only";
  if (RESERVED_WORKFLOW_NAMES.includes(name)) {
    return `${name} is reserved: the console has a page at that address — choose another name`;
  }
  return undefined;
}
