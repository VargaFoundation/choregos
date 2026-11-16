// SPDX-License-Identifier: Apache-2.0
"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { Badge, Button, buttonClasses, Empty, ErrorNote, Table, TBody, TD, TH, THead, TR } from "@/components/ui";
import { api } from "@/lib/api";
import { relative } from "@/lib/format";
import type { Agent, AgentSocial } from "@/lib/types";
import { ScopeBadge } from "./governance";

/**
 * Watch, star and fork counters of an agent (ADR 0152), the way a repository shows them. The server
 * counts: `forks` is the forks the caller may see, so two people can read two numbers.
 */

const NONE: AgentSocial = { watchers: 0, stars: 0, forks: 0, watching: null, starred_by_me: false };

export function SocialBar({ org, agent, forksHref }: { org: string; agent: Agent; forksHref: string }) {
  const client = useQueryClient();
  const social = agent.social ?? NONE;
  const [menu, setMenu] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const change = useMutation({
    mutationFn: (what: () => Promise<AgentSocial>) => what(),
    onSuccess: async () => {
      setError(null);
      await client.invalidateQueries({ queryKey: ["agent", org, agent.slug] });
      await client.invalidateQueries({ queryKey: ["agents", org] });
    },
    onError: (cause) => setError(cause instanceof Error ? cause.message : "refused"),
  });
  const watching = social.watching != null;
  const pick = (what: () => Promise<AgentSocial>) => {
    setMenu(false);
    change.mutate(what);
  };
  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="social-bar">
      <div className="relative">
        <button
          type="button"
          className={buttonClasses("secondary", "sm")}
          aria-haspopup="menu"
          aria-expanded={menu}
          aria-pressed={watching}
          data-testid="watch"
          onClick={() => setMenu(!menu)}
        >
          {watching ? "Watching" : "Watch"} ▾ <span className="font-mono" data-testid="watchers">{social.watchers}</span>
        </button>
        {menu && (
          <div role="menu" aria-label="Watch" className="absolute left-0 z-10 mt-1 w-60 rounded-md border border-line bg-surface p-1 shadow-lg">
            {(
              [
                ["all", "All activity", "Releases, forks, lifecycle changes."],
                ["releases", "Releases only", "New versions and moves of production or testing."],
              ] as const
            ).map(([level, label, hint]) => (
              <button
                key={level}
                type="button"
                role="menuitemradio"
                aria-checked={social.watching === level}
                className="block w-full rounded px-3 py-2 text-left text-sm hover:bg-surface-muted"
                onClick={() => pick(() => api.watchAgent(org, agent.slug, level))}
              >
                <span className="font-medium">{label}</span>
                <span className="block text-xs text-ink-muted">{hint}</span>
              </button>
            ))}
            {watching && (
              <button
                type="button"
                role="menuitem"
                className="block w-full rounded px-3 py-2 text-left text-sm hover:bg-surface-muted"
                onClick={() => pick(() => api.unwatchAgent(org, agent.slug))}
              >
                Unwatch
              </button>
            )}
          </div>
        )}
      </div>
      <button
        type="button"
        className={buttonClasses("secondary", "sm")}
        aria-pressed={social.starred_by_me}
        data-testid="star"
        onClick={() => change.mutate(() => (social.starred_by_me ? api.unstarAgent(org, agent.slug) : api.starAgent(org, agent.slug)))}
      >
        {social.starred_by_me ? "Starred" : "Star"} <span className="font-mono" data-testid="stars">{social.stars}</span>
      </button>
      <Link href={forksHref} className="rounded-md border border-line px-3 py-1 text-xs hover:bg-surface-muted" data-testid="forks-count">
        Forks <span className="font-mono">{social.forks}</span>
      </Link>
      {error && <span role="alert" className="text-xs text-danger">{error}</span>}
    </div>
  );
}

/** The forks tab: who copied the agent, which version, and whether the copy moved on since. */
export function ForksPanel({ org, agent }: { org: string; agent: Agent }) {
  const forks = useQuery({ queryKey: ["agent-forks", org, agent.slug], queryFn: () => api.agentForks(org, agent.slug) });
  if (forks.error) return <ErrorNote>{String(forks.error)}</ErrorNote>;
  if (!forks.data) return <p className="text-sm text-ink-muted">Reading the forks…</p>;
  if (forks.data.items.length === 0) {
    return <Empty title="No fork yet">Nobody you can see has copied this agent.</Empty>;
  }
  return (
    <Table data-testid="forks">
      <THead>
        <TR>
          <TH>Fork</TH>
          <TH>Owner</TH>
          <TH>Copied from</TH>
          <TH>Latest</TH>
          <TH>Created</TH>
        </TR>
      </THead>
      <TBody>
        {forks.data.items.map((fork) => (
          <TR key={fork.slug} data-testid={`fork-${fork.slug}`}>
            <TD>
              <Link href={`/agents/${fork.slug}`} className="font-medium hover:underline">
                {fork.display_name}
              </Link>
              <span className="ml-2 font-mono text-xs text-ink-muted">{fork.slug}</span>
              <span className="ml-2">
                <ScopeBadge agent={fork} />
              </span>
            </TD>
            <TD className="text-xs text-ink-muted">{fork.owner ?? "—"}</TD>
            <TD className="font-mono">v{fork.forked_from_version ?? "?"}</TD>
            <TD className="font-mono">
              v{fork.latest_version}{" "}
              <Badge tone={fork.diverged ? "warn" : "neutral"}>{fork.diverged ? "diverged" : "unchanged"}</Badge>
            </TD>
            <TD className="text-xs text-ink-muted">{fork.created_at ? relative(fork.created_at) : "—"}</TD>
          </TR>
        ))}
      </TBody>
    </Table>
  );
}

const KIND: Record<string, string> = {
  version_published: "published a version",
  production_moved: "moved production",
  candidate_started: "started a candidate",
  candidate_promoted: "promoted the candidate to production",
  candidate_withdrawn: "withdrew the candidate",
  rolled_back: "rolled production back",
  forked: "was forked",
  lifecycle_changed: "changed its lifecycle",
};

/** What happened to the agents you watch (ADR 0152): only what you may still read. */
export function WatchedAgentsCard({ org }: { org: string }) {
  const client = useQueryClient();
  const items = useQuery({ queryKey: ["agent-notifications", org], queryFn: () => api.agentNotifications(org), retry: false });
  const refresh = () => client.invalidateQueries({ queryKey: ["agent-notifications", org] });
  const unread = items.data?.unread ?? 0;
  return (
    <div className="rounded-lg border border-line p-4" data-testid="watched-agents">
      <div className="mb-2 flex items-center justify-between gap-3">
        <h2 className="text-sm font-semibold">Agents you watch</h2>
        <span className="flex items-center gap-3 text-xs text-ink-muted">
          {unread} unread
          {unread > 0 && (
            <Button size="sm" onClick={() => void api.readAllAgentNotifications(org).then(refresh)}>
              Mark all read
            </Button>
          )}
        </span>
      </div>
      {items.error ? (
        <ErrorNote>{String(items.error)}</ErrorNote>
      ) : !items.data ? (
        <p className="text-sm text-ink-muted">Reading…</p>
      ) : items.data.items.length === 0 ? (
        <Empty title="Nothing new">Watch an agent from its page to hear about its releases and forks here.</Empty>
      ) : (
        <ul className="space-y-2">
          {items.data.items.map((item) => (
            <li key={item.id} className="flex flex-wrap items-center gap-2 border-t border-line pt-2 text-sm" data-testid={`agent-news-${item.id}`}>
              {item.read_at == null && <Badge tone="accent">New</Badge>}
              <Link href={`/agents/${item.agent}`} className="font-medium hover:underline">
                {item.agent}
              </Link>
              <span>
                {KIND[item.kind] ?? item.kind}
                {item.version != null ? ` v${item.version}` : ""}
                {item.fork ? ` as ${item.fork}` : ""}
                {item.kind === "lifecycle_changed" && item.detail ? ` to ${item.detail}` : ""}
              </span>
              <span className="text-xs text-ink-muted">
                {item.actor ? `${item.actor} · ` : ""}
                {relative(item.created_at)}
              </span>
              {item.read_at == null && (
                <Button size="sm" onClick={() => void api.readAgentNotification(org, item.id).then(refresh)}>
                  Mark read
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
