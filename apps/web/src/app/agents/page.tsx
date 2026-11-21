// SPDX-License-Identifier: Apache-2.0
"use client";

import { keepPreviousData, useInfiniteQuery, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { AgentCatalogue } from "@/components/agents/catalogue";
import { ChannelBadges } from "@/components/agents/channels";
import { LifecycleBadges } from "@/components/agents/lifecycle";
import { ScopeBadge, ScopeFilter } from "@/components/agents/governance";
import { versionSummary, scopeLabel, type AgentScope } from "@/components/agents/registry";
import { TagFilter, TagList } from "@/components/agents/tags";
import { Glossary } from "@/components/glossary";
import { Badge, Button, Card, Drawer, Empty, ErrorNote, Input, Table, TBody, TD, TH, THead, TR, buttonClasses } from "@/components/ui";
import { OrgPageHeader } from "@/components/org-page-header";
import { api, type AgentsParams } from "@/lib/api";
import { nextCursor } from "@/lib/paging";
import { percent, usd } from "@/lib/format";
import { useSession } from "@/lib/session";

/**
 * The organisation's agents (ADR 0033): the registry, and nothing else (S30-04, plan item 7). A new
 * agent has its own page (`/agents/new`, S39-05) and the catalogue opens in a drawer; AI clients and their tokens live on the AI clients'
 * page. Each column comes from one request: the registry, and the metrics of every agent at once.
 */
export default function AgentsPage() {
  return (
    <Suspense>
      <Agents />
    </Suspense>
  );
}

function Agents() {
  const { org } = useSession();
  // `/agents?catalogue=1`: the way back from the new-agent page's "add one from the catalogue".
  const search = useSearchParams();
  const router = useRouter();
  const catalogue = search.get("catalogue") === "1";
  // `/agents?tag=madr&tag=design`: a filtered list is a link (S40-10); an agent must carry every tag.
  const tags = search.getAll("tag");
  function selectTags(next: string[]) {
    const query = new URLSearchParams(search.toString());
    query.delete("tag");
    for (const tag of next) query.append("tag", tag);
    const text = query.toString();
    router.replace(text ? `/agents?${text}` : "/agents");
  }
  // Every filter is a query parameter and the server pages (ADR 0206): "More" asks for the next page.
  const [awaitingOnly, setOnlyAwaiting] = useState(false);
  const [scope, setScope] = useState<AgentScope | null>(null);
  // Stars and the caller's own stars (ADR 0152): a sort and a filter the server applies.
  const [onlyStarred, setOnlyStarred] = useState(false);
  const [byStars, setByStars] = useState(false);
  // A retired agent is gone for good: it stays readable, but out of the way unless asked for.
  const [showRetired, setShowRetired] = useState(false);
  const [drawer, setDrawer] = useState<"catalogue" | null>(catalogue ? "catalogue" : null);
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const filters: AgentsParams = {
    q: q || undefined,
    scope: scope ?? undefined,
    tag: tags,
    starred: onlyStarred,
    sort: byStars ? "stars" : "name",
    retired: showRetired,
    awaiting_approval: awaitingOnly,
  };
  const agents = useInfiniteQuery({
    queryKey: ["agents", org, filters],
    queryFn: ({ pageParam }) => api.agentsPage(org, { ...filters, cursor: pageParam, limit: 50 }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: nextCursor,
    // The rows stay while a filter's answer is on its way.
    placeholderData: keepPreviousData,
  });
  const shown = agents.data?.pages.flatMap((page) => page.items);
  // The metrics are paged by name too: read pages until the last agent shown is covered (the names go up).
  const metrics = useInfiniteQuery({
    queryKey: ["agent-metrics", org],
    queryFn: ({ pageParam }) => api.agentMetricsList(org, 30, { cursor: pageParam, limit: 100 }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: nextCursor,
  });
  const metricLines = metrics.data?.pages.flatMap((page) => page.items) ?? [];
  const covered = metricLines.at(-1)?.agent ?? "";
  const furthest = (shown ?? []).reduce((max, a) => (a.slug > max ? a.slug : max), "");
  const { hasNextPage: moreMetrics, isFetchingNextPage: fetchingMetrics, fetchNextPage: nextMetrics } = metrics;
  useEffect(() => {
    if (moreMetrics && !fetchingMetrics && covered < furthest) void nextMetrics();
  }, [moreMetrics, fetchingMetrics, nextMetrics, covered, furthest]);
  // Releases waiting for approval: the approver's inbox (S40-02b), as a filter chip.
  const waiting = useQuery({
    queryKey: ["agent-releases-pending", org],
    queryFn: () => api.orgAgentReleases(org, "pending_approval", { limit: 200 }),
    retry: false,
  });
  const awaiting = new Set((waiting.data?.items ?? []).map((r) => r.agent));
  const filtered = q !== "" || scope !== null || onlyStarred || awaitingOnly || tags.length > 0;
  const parAgent = new Map(metricLines.map((m) => [m.agent, m]));
  return (
    <div className="space-y-6">
      <OrgPageHeader title="Agents"
        description="An agent is an object of the organisation: its instructions, model, skills and tools live in versions that never change; a project pins one and may only tighten it."
        actions={
          <>
            <Button onClick={() => setDrawer("catalogue")}>Add from catalogue</Button>
            <Link href="/agents/new" className={buttonClasses("primary")}>
              New agent
            </Link>
          </>
        }
      />
      <Glossary ici="agents" />
      {(shown?.length ?? 0) > 0 || filtered ? (
        <div className="flex flex-wrap items-center gap-3">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              setQ(text.trim());
            }}
          >
            <Input type="search" aria-label="Find an agent" placeholder="Slug or name" value={text} onChange={(e) => setText(e.target.value)} className="max-w-xs" />
          </form>
          <ScopeFilter value={scope} onChange={setScope} />
          {awaiting.size > 0 && (
            <button
              type="button"
              aria-pressed={awaitingOnly}
              onClick={() => setOnlyAwaiting(!awaitingOnly)}
              data-testid="awaiting-chip"
              className={`rounded-pill border px-3 py-1 text-xs ${awaitingOnly ? "border-accent-line bg-accent-soft text-accent-strong" : "border-line text-ink-muted hover:bg-surface-muted"}`}
            >
              Awaiting approval
            </button>
          )}
          <button
            type="button"
            aria-pressed={onlyStarred}
            onClick={() => setOnlyStarred(!onlyStarred)}
            data-testid="starred-chip"
            className={`rounded-pill border px-3 py-1 text-xs ${onlyStarred ? "border-accent-line bg-accent-soft text-accent-strong" : "border-line text-ink-muted hover:bg-surface-muted"}`}
          >
            Starred
          </button>
          <button
            type="button"
            aria-pressed={byStars}
            onClick={() => setByStars(!byStars)}
            data-testid="sort-stars"
            className={`rounded-pill border px-3 py-1 text-xs ${byStars ? "border-accent-line bg-accent-soft text-accent-strong" : "border-line text-ink-muted hover:bg-surface-muted"}`}
          >
            Most starred first
          </button>
          {(
            <button
              type="button"
              aria-pressed={showRetired}
              onClick={() => setShowRetired(!showRetired)}
              data-testid="retired-chip"
              className={`rounded-pill border px-3 py-1 text-xs ${showRetired ? "border-accent-line bg-accent-soft text-accent-strong" : "border-line text-ink-muted hover:bg-surface-muted"}`}
            >
              Show retired
            </button>
          )}
        </div>
      ) : null}
      {shown && <TagFilter agents={shown} value={tags} onChange={selectTags} />}
      <Card title="Registry" padding="none" className="overflow-hidden">
        {agents.error ? (
          <div className="p-5">
            <ErrorNote>{String(agents.error)}</ErrorNote>
          </div>
        ) : !shown ? (
          <p className="p-5 text-sm text-ink-muted">Reading the registry…</p>
        ) : shown.length === 0 && !filtered ? (
          <div className="p-5">
            <Empty title="No agent yet">Create one, or add one from the catalogue.</Empty>
          </div>
        ) : shown.length === 0 ? (
          <div className="p-5">
            {tags.length > 0 ? (
              <Empty title={`No agent tagged ${tags.join(" + ")}`}>
                <button type="button" className="underline" onClick={() => selectTags([])}>
                  Clear the tags
                </button>
                {(scope !== null || awaitingOnly) && " or pick another scope."}
              </Empty>
            ) : (
              <Empty title={awaitingOnly && scope === null ? "No release is waiting for approval" : `No ${scopeLabel(scope ?? undefined).toLowerCase()} agent`}>Pick another scope, or clear the filter.</Empty>
            )}
          </div>
        ) : (
          <Table data-testid="registry">
            <THead>
              <TR>
                <TH>Agent</TH>
                <TH>Owner</TH>
                <TH align="right">Watch / Stars / Forks</TH>
                <TH>Status</TH>
                <TH>Version</TH>
                <TH>Production / Testing</TH>
                <TH align="right">Runs (30 days)</TH>
                <TH align="right">Cost (30 days)</TH>
                <TH align="right">Success rate</TH>
              </TR>
            </THead>
            <TBody>
              {shown.map((agent) => {
                const m = parAgent.get(agent.slug);
                return (
                  <TR key={agent.slug} interactive data-testid={`agent-${agent.slug}`}>
                    <TD>
                      <Link href={`/agents/${agent.slug}`} className="font-medium hover:underline">
                        {agent.display_name}
                      </Link>
                      <span className="ml-2 font-mono text-xs text-ink-muted">{agent.slug}</span>
                      <span className="ml-2">
                        <ScopeBadge agent={agent} />
                      </span>
                      {agent.kind !== "internal" && (
                        <Badge tone="accent" className="ml-2">
                          {agent.kind === "remote" ? "Remote · A2A" : "External"}
                        </Badge>
                      )}
                      {(agent.tags ?? []).length > 0 && (
                        <div className="mt-1">
                          <TagList tags={agent.tags} />
                        </div>
                      )}
                    </TD>
                    <TD className="text-xs text-ink-muted">{agent.owner ?? "—"}</TD>
                    <TD align="right" className="font-mono text-xs" data-testid={`social-${agent.slug}`}>
                      {agent.social?.watchers ?? 0} / {agent.social?.stars ?? 0} / {agent.social?.forks ?? 0}
                    </TD>
                    <TD>
                      <Badge tone={agent.status === "active" ? "ok" : "danger"}>{agent.status}</Badge>
                      <LifecycleBadges agent={agent} />
                    </TD>
                    <TD className="font-mono">
                      v{agent.latest_version}
                      <span className="ml-2 font-sans text-xs text-ink-muted">
                        {versionSummary(agent.versions?.find((v) => v.version === agent.latest_version)?.spec)}
                      </span>
                    </TD>
                    <TD>
                      <ChannelBadges agent={agent} pending={awaiting.has(agent.slug) || agent.pending_release != null} />
                    </TD>
                    <TD align="right" className="font-mono">
                      {m ? m.runs : "—"}
                    </TD>
                    <TD align="right" className="font-mono">
                      {m ? usd(m.cost_usd) : "—"}
                    </TD>
                    <TD align="right" className="font-mono">
                      {m?.success_rate != null ? percent(m.success_rate) : "—"}
                    </TD>
                  </TR>
                );
              })}
            </TBody>
          </Table>
        )}
        {agents.hasNextPage && (
          <div className="p-3">
            <Button size="sm" onClick={() => void agents.fetchNextPage()} disabled={agents.isFetchingNextPage}>
              More
            </Button>
          </div>
        )}
      </Card>
      <Drawer open={drawer === "catalogue"} onClose={() => setDrawer(null)} title="Add from catalogue">
        <AgentCatalogue org={org} />
      </Drawer>
    </div>
  );
}
