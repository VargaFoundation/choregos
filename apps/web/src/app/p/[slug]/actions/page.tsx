// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { use } from "react";
import { Statut, propose } from "@/components/actions/statut";
import { Card, Empty, ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";
import { relative } from "@/lib/format";

/** Les actions gouvernées du projet : proposées, décidées, exécutées — et ce qui a été défait. */
export default function ProjectActionsPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const actions = useQuery({ queryKey: ["actions", slug], queryFn: () => api.projectActions(slug) });
  if (actions.error) return <ErrorNote>{String(actions.error)}</ErrorNote>;
  return (
    <Card title="governed actions">
      {!actions.data ? (
        <p className="text-sm text-ink-muted">reading…</p>
      ) : actions.data.length === 0 ? (
        <Empty title="no action yet">a tool under approval, or a step that writes elsewhere, proposes one.</Empty>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm" data-testid="actions">
            <thead className="text-left text-xs text-ink-muted">
              <tr>
                <th className="py-1 font-normal">action</th>
                <th className="py-1 font-normal">status</th>
                <th className="py-1 font-normal">proposed by</th>
                <th className="py-1 font-normal">effects</th>
              </tr>
            </thead>
            <tbody>
              {actions.data.map((action) => (
                <tr key={action.id} className="border-t border-line">
                  <td className="py-2">
                    <Link href={`/p/${slug}/actions/${action.id}`} className="font-medium hover:underline">
                      {action.title}
                    </Link>
                    <span className="ml-2 text-xs text-ink-muted">
                      {action.created_at ? relative(action.created_at) : ""}
                    </span>
                  </td>
                  <td>
                    <Statut statut={action.status} />
                  </td>
                  <td className="text-xs">{propose(action.proposed_by)}</td>
                  <td className="text-xs text-ink-muted">
                    {(action.journal ?? []).filter((e) => e.status === "done").length}/{(action.effects ?? []).length}{" "}
                    done
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
