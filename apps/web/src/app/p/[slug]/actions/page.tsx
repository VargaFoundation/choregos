// SPDX-License-Identifier: Apache-2.0
"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import Link from "next/link";
import { use } from "react";
import { Status, propose } from "@/components/actions/status";
import { Button, Card, Empty, ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";
import { relative } from "@/lib/format";

/** The project's governed actions: proposed, decided, executed, and what has been undone. */
export default function ProjectActionsPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const actions = useInfiniteQuery({
    queryKey: ["actions", slug],
    queryFn: ({ pageParam }) => api.projectActions(slug, { cursor: pageParam, limit: 50 }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => (last.meta.has_more ? (last.meta.next_cursor ?? undefined) : undefined),
  });
  const rows = actions.data?.pages.flatMap((page) => page.items);
  if (actions.error) return <ErrorNote>{String(actions.error)}</ErrorNote>;
  return (
    <Card title="Governed actions">
      {!actions.data ? (
        <p className="text-sm text-ink-muted">Reading…</p>
      ) : rows?.length === 0 ? (
        <Empty title="No action yet">A tool under approval, or a step that writes elsewhere, proposes one.</Empty>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm" data-testid="actions">
            <thead className="text-left text-xs text-ink-muted">
              <tr>
                <th className="py-1 font-normal">Action</th>
                <th className="py-1 font-normal">Status</th>
                <th className="py-1 font-normal">Proposed by</th>
                <th className="py-1 font-normal">Effects</th>
              </tr>
            </thead>
            <tbody>
              {rows?.map((action) => (
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
                    <Status statut={action.status} />
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
          {actions.hasNextPage && (
            <div className="mt-3 flex justify-center">
              <Button size="sm" onClick={() => void actions.fetchNextPage()} disabled={actions.isFetchingNextPage}>
                More
              </Button>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
