// SPDX-License-Identifier: Apache-2.0
"use client";

import type { Membership } from "@/lib/types";

/** The roles, from least to most (`rbac.py`). */
export const ROLES = ["viewer", "developer", "release_captain", "project_owner", "org_admin"] as const;
export type Role = (typeof ROLES)[number];

/** The roles a project row may carry: `org_admin` is the organisation's (S30-06; the API refuses it). */
export const PROJECT_ROLES: readonly Role[] = ROLES.filter((role) => role !== "org_admin");

export const ROLE_LABELS: Record<Role, string> = {
  viewer: "Viewer",
  developer: "Developer",
  release_captain: "Release captain",
  project_owner: "Project owner",
  org_admin: "Organisation admin",
};

export function rank(role: string): number {
  return ROLES.indexOf(role as Role);
}

/** One person of the organisation: their organisation role, and their project roles. */
export interface Person {
  userId: string;
  email: string;
  orgRole: Role | null;
  projects: { slug: string; role: Role }[];
}

/** The organisation's memberships, one row per person (S30-06): a person appears once. */
export function peopleOf(memberships: Membership[]): Person[] {
  const people = new Map<string, Person>();
  for (const m of memberships) {
    const key = m.user_id ?? m.email ?? "?";
    const person = people.get(key) ?? { userId: m.user_id ?? "", email: m.email ?? key, orgRole: null, projects: [] };
    if (m.project_slug) person.projects.push({ slug: m.project_slug, role: m.role as Role });
    else person.orgRole = m.role as Role;
    people.set(key, person);
  }
  return [...people.values()]
    .map((p) => ({ ...p, projects: p.projects.sort((a, b) => a.slug.localeCompare(b.slug)) }))
    .sort((a, b) => a.email.localeCompare(b.email));
}

/**
 * What RBAC does, said where roles are given (S30-06, `rbac.py`): a project role adds to the
 * organisation role; it never takes anything away. Restricting someone on one project is an RBAC
 * change, not something this screen can do.
 */
export function AdditiveRolesNote() {
  return (
    <p className="max-w-3xl text-sm text-ink-muted" data-testid="roles-additifs">
      A project role adds to the person&apos;s organisation role on that project; it cannot take anything away. Someone
      who is a developer of the organisation stays at least a developer on every project.
    </p>
  );
}
