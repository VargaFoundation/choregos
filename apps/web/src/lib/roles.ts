// SPDX-License-Identifier: Apache-2.0

/** The roles, from least to most (`rbac.py`), under their neutral names (ADR 0181 C1). */
export const ROLES = ["viewer", "contributor", "approver", "project_owner", "org_admin"] as const;
export type Role = (typeof ROLES)[number];

/** The former names the API may still answer until 0.30.0, and the role each names (ADR 0181 C1). */
const FORMER_NAMES: Readonly<Record<string, Role>> = { developer: "contributor", release_captain: "approver" };

/** A role as the API answered it, under its neutral name. */
export function neutralRole(role: string): Role {
  return (FORMER_NAMES[role] ?? role) as Role;
}
