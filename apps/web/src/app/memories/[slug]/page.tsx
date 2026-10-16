// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { use, useState } from "react";
import { Button, Card, Empty, ErrorNote, EtatDeLecture, Eyebrow, Heading, Input, LEGENDE, Select } from "@/components/ui";
import { api } from "@/lib/api";
import { shortDate } from "@/lib/format";
import { reachLabel } from "@/lib/memories";
import { useSession } from "@/lib/session";

/** One global memory: search it, decide what agents proposed, change its reach, archive it, see who uses it. */
export default function MemoryPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const { org, me } = useSession();
  const client = useQueryClient();
  const memory = useQuery({ queryKey: ["memory-store", org, slug], queryFn: () => api.memory(org, slug) });
  const projects = useQuery({ queryKey: ["projects", org], queryFn: () => api.projects(org) });
  const admin = (me?.memberships ?? []).some((m) => m.org === org && !m.project_slug && m.role === "org_admin");
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const results = useQuery({
    queryKey: ["memory-search", org, slug, submitted],
    queryFn: () => api.searchGlobalMemory(org, slug, submitted),
    enabled: submitted.length > 0,
  });
  const pending = useQuery({
    queryKey: ["memory-pending-global", org, slug],
    queryFn: () => api.globalMemoryPending(org, slug),
    enabled: admin,
  });
  const [error, setError] = useState<string | null>(null);
  const [reach, setReach] = useState<"org" | "projects" | null>(null);
  const [chosen, setChosen] = useState<string[] | null>(null);

  const current = memory.data;
  const effectiveReach = reach ?? current?.reach ?? "org";
  const effectiveProjects = chosen ?? current?.projects ?? [];

  async function refresh() {
    await client.invalidateQueries({ queryKey: ["memory-store", org, slug] });
    await client.invalidateQueries({ queryKey: ["memories", org] });
  }

  async function decide(id: string, action: "accept" | "reject") {
    setError(null);
    try {
      await api.decideGlobalMemory(org, slug, id, action);
      await client.invalidateQueries({ queryKey: ["memory-pending-global", org, slug] });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "decision refused");
    }
  }

  async function save(body: Parameters<typeof api.updateMemory>[2]) {
    setError(null);
    try {
      await api.updateMemory(org, slug, body);
      setReach(null);
      setChosen(null);
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "change refused");
    }
  }

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <Link href="/memories" className="text-xs underline">
          Memories
        </Link>
        <Eyebrow>Organisation memory</Eyebrow>
        <Heading as="h1" size="xl">
          {current?.display_name ?? slug}
        </Heading>
        <p className="font-mono text-xs text-ink-muted">
          global:{slug}
          {current?.status === "archived" && " · archived"}
        </p>
        {current?.description && <p className="max-w-3xl text-sm text-ink-muted">{current.description}</p>}
        {memory.error && <ErrorNote>{String(memory.error)}</ErrorNote>}
      </div>
      {error && <ErrorNote>{error}</ErrorNote>}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Search">
          <form
            className="mb-3 flex items-end gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              setSubmitted(query);
            }}
          >
            <label className="flex min-w-0 flex-1 flex-col gap-1">
              <span className="text-xs text-ink-muted">Search this memory</span>
              <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="TLS, incident, convention…" className="flex-1" />
            </label>
            <Button type="submit" tone="primary">
              Search
            </Button>
          </form>
          <ul className="space-y-3">
            {(results.data ?? []).map((m) => (
              <li key={m.id} className="raised border border-line p-3">
                <p className={LEGENDE}>{m.kind}</p>
                <p className="font-mono text-xs">{m.subject}</p>
                <p className="mt-1 text-sm">{m.content}</p>
                <p className="mt-1 text-xs text-ink-muted">Valid from {shortDate(m.valid_from)}</p>
              </li>
            ))}
          </ul>
          <EtatDeLecture lecture={results} quoi="the memory" />
          {submitted && results.data?.length === 0 && <Empty>No fact for “{submitted}”</Empty>}
        </Card>
        <Card title="Proposed by agents">
          {!admin ? (
            <p className="text-sm text-ink-muted">An organisation administrator decides what agents propose to this memory.</p>
          ) : (
            <>
              <ul className="space-y-3" data-testid="memory-proposals">
                {(pending.data ?? [])
                  .filter((m) => m.id)
                  .map((m) => (
                    <li key={m.id} className="rounded border border-line border-l-2 border-l-warn bg-surface p-3">
                      <p className="font-mono text-xs">{m.subject}</p>
                      <p className="mt-1 text-sm">{m.content}</p>
                      <p className="mt-1 text-xs text-ink-muted">Proposed by run {m.proposed_by ?? "—"}</p>
                      <div className="mt-2 flex gap-2">
                        <Button tone="primary" onClick={() => decide(m.id ?? "", "accept")}>
                          Accept
                        </Button>
                        <Button tone="danger" onClick={() => decide(m.id ?? "", "reject")}>
                          Reject
                        </Button>
                      </div>
                    </li>
                  ))}
              </ul>
              <EtatDeLecture lecture={pending} quoi="the proposals" />
              {pending.data?.length === 0 && <Empty>Nothing waits for a decision</Empty>}
            </>
          )}
        </Card>
      </div>
      <Card title="Reach and use">
        <div className="space-y-3 text-sm">
          <p>
            Read by the runs of <strong>{current ? reachLabel(current) : "…"}</strong>.
          </p>
          {admin && current && (
            <div className="space-y-2">
              <label className="block max-w-xs space-y-1">
                <span className="text-xs text-ink-muted">Reach</span>
                <Select value={effectiveReach} onChange={(e) => setReach(e.target.value as "org" | "projects")}>
                  <option value="org">Every project</option>
                  <option value="projects">Some projects</option>
                </Select>
              </label>
              {effectiveReach === "projects" && (
                <fieldset className="space-y-1">
                  <legend className="text-xs text-ink-muted">Projects that read it</legend>
                  {(projects.data?.items ?? []).map((p) => (
                    <label key={p.slug} className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        checked={effectiveProjects.includes(p.slug)}
                        onChange={(e) =>
                          setChosen(e.target.checked ? [...effectiveProjects, p.slug] : effectiveProjects.filter((s) => s !== p.slug))
                        }
                      />
                      {p.name ?? p.slug}
                    </label>
                  ))}
                </fieldset>
              )}
              <div className="flex gap-2">
                <Button tone="primary" onClick={() => save({ reach: effectiveReach, projects: effectiveReach === "projects" ? effectiveProjects : [] })}>
                  Save the reach
                </Button>
                <Button onClick={() => save({ status: current.status === "archived" ? "active" : "archived" })}>
                  {current.status === "archived" ? "Restore" : "Archive"}
                </Button>
              </div>
            </div>
          )}
          <div>
            <p className="text-xs text-ink-muted">Used by</p>
            {(current?.used_by ?? []).length === 0 ? (
              <p>No agent binds it yet.</p>
            ) : (
              <ul className="flex flex-wrap gap-x-3">
                {(current?.used_by ?? []).map((u) => (
                  <li key={u.agent}>
                    <Link href={`/agents/${u.agent}`} className="underline">
                      {u.agent} v{u.version}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </Card>
    </div>
  );
}
