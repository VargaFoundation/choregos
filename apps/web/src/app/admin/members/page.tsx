// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { MemberLinkedAccounts } from "@/components/accounts/member-linked-accounts";
import { peopleOf } from "@/components/members";
import { MembersTable } from "@/components/members-table";
import { Card } from "@/components/ui";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";

/**
 * The organisation's members (S30-06): one row per person, their role in the organisation and
 * their projects. The last administrator cannot leave — the API refuses it (409), and the screen says so.
 * An organisation administrator also sees each member's accounts on code forges, verified by the member or
 * declared, and declares one (ADR 0154).
 */
export default function MembersPage() {
  const { me, org } = useSession();
  const orgAdmin = (me?.memberships ?? []).some((m) => m.org === org && !m.project_slug && m.role === "org_admin");
  const members = useQuery({ queryKey: ["members", org, ""], queryFn: () => api.members(org), enabled: orgAdmin });
  const people = peopleOf(members.data ?? []).filter((p) => p.userId);
  return (
    <div className="space-y-4">
      <MembersTable />
      {orgAdmin && people.length > 0 && (
        <Card title="Linked accounts">
          <ul className="space-y-4">
            {people.map((person) => (
              <li key={person.userId} data-testid={`linked-accounts-${person.email}`}>
                <p className="mb-1 font-medium">{person.email}</p>
                <MemberLinkedAccounts org={org} userId={person.userId} />
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
