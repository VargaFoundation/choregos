// SPDX-License-Identifier: Apache-2.0
"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { OrgPageHeader } from "@/components/org-page-header";
import { Badge, Button, Card, Empty, ReadState, buttonClasses } from "@/components/ui";
import { assistantsApi } from "@/lib/assistants-api";
import { relative, usd } from "@/lib/format";

/** My conversations with assistants, the latest first. */
export function ConversationList() {
  const client = useQueryClient();
  const conversations = useQuery({ queryKey: ["my-conversations"], queryFn: assistantsApi.myConversations });
  const remove = useMutation({
    mutationFn: (id: string) => assistantsApi.deleteConversation(id),
    onSuccess: () => client.invalidateQueries({ queryKey: ["my-conversations"] }),
  });
  const items = conversations.data?.items ?? [];
  return (
    <div className="space-y-6">
      <OrgPageHeader
        title="Conversations"
        eyebrow="Assistants"
        actions={
          <Link href="/assistants" className={buttonClasses("secondary")}>
            Assistants
          </Link>
        }
      />
      <ReadState lecture={conversations} quoi="your conversations" />
      {conversations.data && items.length === 0 && (
        <Empty title="No conversation yet">Open an assistant and start one.</Empty>
      )}
      <div className="space-y-3">
        {items.map((c) => (
          <Card key={c.id} padding="sm">
            <div className="flex items-center justify-between gap-4">
              <div className="min-w-0 space-y-1">
                <Link href={`/conversations/${encodeURIComponent(c.id)}`} className="font-medium">
                  {c.title ?? "Untitled conversation"}
                </Link>
                <p className="text-xs text-ink-muted">
                  {c.agent} · {c.turns} turns · {usd(c.cost_usd)} · {relative(c.last_activity_at)}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <Badge tone={c.status === "failed" ? "failed" : "neutral"}>{c.status}</Badge>
                <Button tone="danger" size="sm" onClick={() => remove.mutate(c.id)} disabled={remove.isPending}>
                  Delete
                </Button>
              </div>
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
