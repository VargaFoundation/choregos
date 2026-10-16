// SPDX-License-Identifier: Apache-2.0
import type { Role } from "@/components/members";
import type { Edition, Membership } from "@/lib/types";

/** One line of "your roles": a scope and the role held there. */
export interface MembershipLine {
  scope: string;
  role: Role;
}

/** The lines of one organisation; `org` is null when the heading would only repeat the one organisation. */
export interface MembershipGroup {
  org: string | null;
  lines: MembershipLine[];
}

/**
 * True when the instance may hold several organisations. An edition not read yet counts as
 * multi-organisation: "platform" is never claimed before it is proven (S40-07).
 */
export function isMultiOrg(edition: Pick<Edition, "features"> | undefined): boolean {
  if (!edition) return true;
  return (edition.features ?? []).includes("multi_org");
}

/**
 * The signed-in person's roles by scope, so that the organisation slug is not repeated on every line.
 * Community edition (one organisation, whose administrator administers the instance): `platform`
 * and `project/<slug>` in a single group. Multi-organisation: one group per organisation, the
 * organisation-level line reading `organisation` (an organisation administrator is not a platform one).
 */
export function membershipGroups(
  memberships: Array<Pick<Membership, "org" | "project_slug" | "role">>,
  options: { multiOrg: boolean; currentOrg: string },
): MembershipGroup[] {
  const { multiOrg, currentOrg } = options;
  const orgLabel = multiOrg ? "organisation" : "platform";
  const orgs = multiOrg
    ? [...new Set(memberships.map((m) => m.org))].sort(
        (a, b) => Number(b === currentOrg) - Number(a === currentOrg) || a.localeCompare(b),
      )
    : [currentOrg];
  const groups = orgs.map((org): MembershipGroup => {
    const own = memberships.filter((m) => m.org === org);
    const orgLines = own
      .filter((m) => !m.project_slug)
      .map((m): MembershipLine => ({ scope: orgLabel, role: m.role as Role }));
    const projectLines = own
      .filter((m) => m.project_slug)
      .sort((a, b) => (a.project_slug as string).localeCompare(b.project_slug as string))
      .map((m): MembershipLine => ({ scope: `project/${m.project_slug}`, role: m.role as Role }));
    return { org: multiOrg ? org : null, lines: [...orgLines, ...projectLines] };
  });
  return groups.filter((group) => group.lines.length > 0);
}
