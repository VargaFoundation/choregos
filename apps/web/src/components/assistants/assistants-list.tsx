// SPDX-License-Identifier: Apache-2.0
"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { OrgPageHeader } from "@/components/org-page-header";
import { Badge, Button, Card, Empty, ErrorNote, ReadState, buttonClasses } from "@/components/ui";
import { assistantsApi, type MyAssistant } from "@/lib/assistants-api";

const TONE = { ready: "succeeded", incomplete: "waiting", suspended: "neutral" } as const;

/** The assistants of my audience: converse with one, or open what it remembers and does for me. */
export function AssistantsList() {
  const router = useRouter();
  const assistants = useQuery({ queryKey: ["my-assistants"], queryFn: assistantsApi.myAssistants });
  const [error, setError] = useState<string | null>(null);
  const open = useMutation({
    mutationFn: (a: MyAssistant) => assistantsApi.openConversation(a.org, a.slug),
    onSuccess: (conversation, a) =>
      router.push(`/conversations/${encodeURIComponent(conversation.id)}?assistant=${a.org}/${a.slug}`),
    onError: (cause) => setError(cause instanceof Error ? cause.message : "Could not open the conversation"),
  });
  const items = assistants.data?.items ?? [];
  return (
    <div className="space-y-6">
      <OrgPageHeader
        title="Assistants"
        eyebrow="Talk to"
        description="The assistants your organisation has made available to you."
        actions={
          <>
            <Link href="/conversations" className={buttonClasses("secondary")}>
              My conversations
            </Link>
            <Link href="/assistants/manage" className={buttonClasses("ghost")}>
              Manage installations
            </Link>
          </>
        }
      />
      <ReadState lecture={assistants} quoi="your assistants" />
      {error && <ErrorNote>{error}</ErrorNote>}
      {assistants.data && items.length === 0 && (
        <Empty title="No assistant yet">No assistant is available to your groups.</Empty>
      )}
      <div className="grid gap-4 md:grid-cols-2">
        {items.map((a) => (
          <Card key={`${a.org}/${a.slug}`} title={a.display_name ?? a.name} verbatimTitle>
            <div className="space-y-3">
              <Badge tone={TONE[a.status]}>{a.status}</Badge>
              {a.description && <p className="text-sm text-ink-muted">{a.description}</p>}
              <div className="flex flex-wrap gap-2">
                <Button
                  tone="primary"
                  disabled={a.status !== "ready" || open.isPending}
                  onClick={() => {
                    setError(null);
                    open.mutate(a);
                  }}
                >
                  Converse
                </Button>
                <Link
                  href={`/assistants/${encodeURIComponent(a.org)}/${encodeURIComponent(a.slug)}`}
                  className={buttonClasses("secondary")}
                >
                  Memory, accounts and tasks
                </Link>
              </div>
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
