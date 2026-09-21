"use client";

import { Heading } from "@varga/design-system";
import { useQuery } from "@tanstack/react-query";
import { Card, Empty, StateBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { shortDate } from "@/lib/format";

/** Administration : backends agents, exécuteurs, audit. Lecture seule ici, actions par l'API. */
export default function AdminPage() {
  const audit = useQuery({ queryKey: ["audit"], queryFn: () => api.audit() });
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.me() });

  return (
    <div className="space-y-4">
      <Heading as="h1" size="xl">
        administration
      </Heading>

      <Card title="session">
        <p className="text-sm">
          {me.data?.display_name} — {me.data?.email}
        </p>
        <ul className="mt-2 space-y-1 text-xs text-ink-muted">
          {(me.data?.memberships ?? []).map((membership, index) => (
            <li key={index}>
              {membership.org}
              {membership.project_slug ? `/${membership.project_slug}` : ""} : {membership.role}
            </li>
          ))}
        </ul>
      </Card>

      <Card title="journal d'audit">
        <table>
          <thead>
            <tr>
              <th>quand</th>
              <th>acteur</th>
              <th>action</th>
              <th>cible</th>
            </tr>
          </thead>
          <tbody>
            {(audit.data?.items ?? []).map((entry) => (
              <tr key={entry.id}>
                <td className="text-xs text-ink-muted">{shortDate(entry.ts)}</td>
                <td>
                  <StateBadge state={entry.actor_kind} display={entry.actor_kind} kind="work" />{" "}
                  <span className="text-xs">{entry.actor_id ?? "—"}</span>
                </td>
                <td className="font-mono text-xs">{entry.action}</td>
                <td className="text-xs text-ink-muted">
                  {entry.target_type}
                  {entry.target_id ? ` ${entry.target_id.slice(0, 8)}` : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {(audit.data?.items ?? []).length === 0 && <Empty>aucune entrée d&apos;audit</Empty>}
      </Card>
    </div>
  );
}
