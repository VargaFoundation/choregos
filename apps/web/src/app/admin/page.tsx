// SPDX-License-Identifier: Apache-2.0
"use client";

import { OrganisationCard } from "@/components/admin/organisation-card";
import { PolicyCeilingCard } from "@/components/admin/policy-ceiling";
import { OrgToolScriptsCard } from "@/components/admin/tool-scripts";
import { useSession } from "@/lib/session";

/**
 * The administration overview: what one administers for the whole organisation. What belongs to the
 * signed-in person (their profile, roles, preferences and access tokens) is on "My account"
 * (`/account`), reached from the menu under their name. Members, the audit, the platform, the edition
 * and the sections of the extensions have their own sub-page (ADR 0032).
 */
export default function AdminPage() {
  const { me, org } = useSession();
  const orgAdmin = (me?.memberships ?? []).some((m) => m.org === org && !m.project_slug && m.role === "org_admin");
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <OrganisationCard org={org} canEdit={orgAdmin} />
      <PolicyCeilingCard org={org} canEdit={orgAdmin} />
      <OrgToolScriptsCard org={org} canEdit={orgAdmin} />
    </div>
  );
}
