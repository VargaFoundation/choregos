// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { AssistantMemory } from "@/components/assistants/assistant-memory";
import { AssistantTasks } from "@/components/assistants/assistant-tasks";
import { ConnectedAccounts } from "@/components/assistants/connected-accounts";
import { MarkdownView } from "@/components/markdown-view";
import { OrgPageHeader } from "@/components/org-page-header";
import { Badge, Card, Empty, ErrorNote, ReadState, buttonClasses, tabClasses } from "@/components/ui";
import { ConfirmedGesture } from "@/components/confirmed-gesture";
import { assistantsApi, type InboxItem, type MyAssistant } from "@/lib/assistants-api";
import { relative } from "@/lib/format";

const TABS = ["Memory", "Connected accounts", "Tasks", "Inbox", "Sources"] as const;
type Tab = (typeof TABS)[number];

/** The sources an assistant can use: which needs it has, which are optional, and under whose identity. */
export function AssistantSources({ assistant }: { assistant: MyAssistant }) {
  const label = { service: "the organisation's service account", user_exchange: "your identity", user_consent: "your connected account" };
  if ((assistant.needs ?? []).length === 0) return <Empty title="No source">This assistant reads nothing outside itself.</Empty>;
  return (
    <ul className="space-y-2">
      {(assistant.needs ?? []).map((n) => (
        <li key={n.need}>
          <Card padding="sm" title={n.need.replaceAll("_", " ")}>
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <Badge tone={n.required ? "ok" : "neutral"}>{n.required ? "Always on" : "Optional"}</Badge>
              <Badge tone="info">{n.access}</Badge>
              <span className="text-ink-muted">Acts as {label[n.identity]}</span>
              {n.identity === "user_consent" && (
                <span className="text-ink-muted">
                  — turns on once you connect it under Connected accounts.
                </span>
              )}
            </div>
          </Card>
        </li>
      ))}
    </ul>
  );
}

/** What the assistant's tasks delivered to me. */
export function AssistantInbox({ slug }: { slug: string }) {
  const inbox = useQuery({ queryKey: ["my-inbox"], queryFn: assistantsApi.inbox });
  const items = (inbox.data?.items ?? []).filter((i) => i.installation === slug);
  return (
    <div className="space-y-3">
      <ReadState lecture={inbox} quoi="your inbox" />
      {inbox.data && items.length === 0 && <Empty title="Nothing delivered">Task results will appear here.</Empty>}
      {items.map((item) => (
        <InboxCard key={item.id} item={item} />
      ))}
    </div>
  );
}

function InboxCard({ item }: { item: InboxItem }) {
  const text =
    typeof item.content === "string" ? item.content : item.content === undefined ? "" : JSON.stringify(item.content, null, 2);
  return (
    <Card padding="sm" title={item.name} verbatimTitle eyebrow={`${item.task} · v${item.version} · ${relative(item.created_at)}`}>
      {item.media_type.includes("markdown") ? (
        <MarkdownView text={text} />
      ) : (
        <pre className="overflow-x-auto text-xs">{text}</pre>
      )}
    </Card>
  );
}

/** One assistant, for me: what it remembers, my accounts, its tasks, what it delivered, its sources. */
export function AssistantWorkspace({ org, slug }: { org: string; slug: string }) {
  const mine = useQuery({ queryKey: ["my-assistants"], queryFn: assistantsApi.myAssistants });
  const [tab, setTab] = useState<Tab>("Memory");
  const [erased, setErased] = useState<string | null>(null);
  const assistant = (mine.data?.items ?? []).find((a) => a.org === org && a.slug === slug);
  return (
    <div className="space-y-6">
      <OrgPageHeader
        title={assistant?.display_name ?? assistant?.name ?? slug}
        eyebrow="Assistants"
        description={assistant?.description ?? undefined}
        actions={
          <Link href="/assistants" className={buttonClasses("secondary")}>
            All assistants
          </Link>
        }
      />
      <ReadState lecture={mine} quoi="this assistant" />
      {mine.data && !assistant && <ErrorNote>This assistant is not available to you.</ErrorNote>}
      {assistant && (
        <>
          <div role="tablist" className="flex gap-4 border-b border-line">
            {TABS.map((name) => (
              <button key={name} role="tab" aria-selected={tab === name} className={tabClasses(tab === name)} onClick={() => setTab(name)}>
                {name}
              </button>
            ))}
          </div>
          {tab === "Memory" && (
            <div className="space-y-4">
              <AssistantMemory org={org} slug={slug} spaces={(assistant.memory_spaces ?? []).map((s) => s.name)} />
              <ConfirmedGesture
                question="Erase every conversation and memory this assistant holds about you? This cannot be undone."
                confirmer="Erase my data"
                action={async () => {
                  const done = await assistantsApi.eraseMyData(org, slug);
                  setErased(`Erased ${done.conversations} conversations and ${done.memory_facts} remembered facts.`);
                }}
              >
                Erase everything about me
              </ConfirmedGesture>
              {erased && <p className="text-sm text-ink-muted">{erased}</p>}
            </div>
          )}
          {tab === "Connected accounts" && <ConnectedAccounts org={org} />}
          {tab === "Tasks" && <AssistantTasks org={org} slug={slug} tasks={assistant.tasks ?? []} />}
          {tab === "Inbox" && <AssistantInbox slug={slug} />}
          {tab === "Sources" && <AssistantSources assistant={assistant} />}
        </>
      )}
    </div>
  );
}
