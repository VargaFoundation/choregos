// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { SettingsSection } from "@/components/project-settings/section";
import { Card } from "@/components/ui";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { Membership } from "@/lib/types";

/** `2 project owners, 1 developer`: the members of the project, counted by role. */
export function countByRole(members: Membership[]): string {
  const counts = new Map<string, number>();
  for (const m of members) counts.set(m.role, (counts.get(m.role) ?? 0) + 1);
  return [...counts.entries()].map(([role, n]) => `${n} ${role.replaceAll("_", " ")}${n > 1 ? "s" : ""}`).join(", ");
}

/** Who works on the project. The members and the groups have their own pages: this section counts and points. */
export function PeopleSection({ slug }: { slug: string }) {
  const { org } = useSession();
  const members = useQuery({ queryKey: ["members", org, slug], queryFn: () => api.members(org, slug) });
  const readiness = useQuery({ queryKey: ["readiness", slug], queryFn: () => api.projectReadiness(slug) });
  const gaps = (readiness.data?.items ?? []).filter((i) => i.section === "people" && i.state !== "done" && i.state !== "not_needed");
  const projectMembers = (members.data ?? []).filter((m) => m.project_slug === slug);
  return (
    <SettingsSection
      id="people"
      title="People"
      lead="The people and the identity-provider groups the workflows ask for. They are managed in Members; nothing is duplicated here."
    >
      <Card title="Members">
        <p className="text-sm" data-testid="people-count">
          {projectMembers.length === 0 ? "No member is assigned to this project yet." : `${projectMembers.length} on this project: ${countByRole(projectMembers)}.`}
        </p>
        {gaps.length > 0 && (
          <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs text-warn" data-testid="people-gaps">
            {gaps.map((gap) => (
              <li key={gap.id}>
                {gap.title}: <span className="text-ink-muted">{gap.detail}</span>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-3 text-xs">
          <Link href={`/p/${slug}/members`}>Open Members</Link>
        </p>
      </Card>
    </SettingsSection>
  );
}
