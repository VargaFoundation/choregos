// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { use, useState } from "react";
import { MemoryReimport } from "@/components/memory-reimport";
import { Button, Card, Drawer, Empty, ErrorNote, EtatDeLecture, Input, LEGENDE, Select } from "@/components/ui";
import { api, type MemoryChoice } from "@/lib/api";
import { shortDate } from "@/lib/format";
import { IMPLICIT, reachLabel } from "@/lib/memories";
import { useSession } from "@/lib/session";

/**
 * A project's memories (S40-11, ADR 0075): its shared memory, what each agent learnt on its own in the
 * project, its named memories, and — read-only here — the organisation's that reach it. Owners create
 * named memories; the organisation's are decided in the registry.
 */
export default function MemoryPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const { org, me } = useSession();
  const queryClient = useQueryClient();
  const memories = useQuery({ queryKey: ["project-memories", slug], queryFn: () => api.projectMemories(slug) });
  const agents = useQuery({ queryKey: ["agents", org], queryFn: () => api.agents(org) });
  // `shared`, `own:<agent>`, `memory:<slug>` (named) or `global:<slug>` (read-only here).
  const [chosen, setChosen] = useState("shared");
  const [agent, setAgent] = useState("");
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ slug: "", name: "", description: "" });

  // An org_admin, or this project's owner: they hold `project:write` and `memory:write` here, so they
  // create project memories and re-import the memory.
  const canWrite = (me?.memberships ?? []).some(
    (m) => m.org === org && ((!m.project_slug && m.role === "org_admin") || (m.project_slug === slug && m.role === "project_owner")),
  );
  const named = (memories.data ?? []).filter((m) => m.scope === "project" && !m.implicit);
  const global = (memories.data ?? []).filter((m) => m.scope === "global");
  const isGlobal = chosen.startsWith("global:");
  const globalSlug = isGlobal ? chosen.slice("global:".length) : "";
  const which: MemoryChoice =
    chosen === "own" ? { memory: "own", agent } : chosen.startsWith("memory:") ? { memory: chosen.slice("memory:".length) } : {};
  const ready = chosen !== "own" || agent !== "";

  const results = useQuery({
    queryKey: ["memory", slug, chosen, agent, submitted],
    queryFn: () => (isGlobal ? api.searchGlobalMemory(org, globalSlug, submitted) : api.memorySearch(slug, submitted, which)),
    enabled: submitted.length > 0 && ready,
  });
  const pending = useQuery({
    queryKey: ["memory-pending", slug, chosen, agent],
    queryFn: () => api.memoryPending(slug, which),
    enabled: !isGlobal && ready,
  });

  async function decide(id: string, action: "accept" | "reject") {
    setError(null);
    try {
      await api.memoryDecide(slug, id, action, which);
      queryClient.invalidateQueries({ queryKey: ["memory-pending", slug] });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "decision refused");
    }
  }

  async function create() {
    setError(null);
    try {
      await api.createProjectMemory(slug, { slug: form.slug, display_name: form.name || form.slug, description: form.description || undefined });
      await queryClient.invalidateQueries({ queryKey: ["project-memories", slug] });
      await queryClient.invalidateQueries({ queryKey: ["memories", org] });
      setOpen(false);
      setForm({ slug: "", name: "", description: "" });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Creation refused");
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex min-w-0 flex-col gap-1">
          <span className="text-xs text-ink-muted">Memory</span>
          <Select value={chosen} onChange={(e) => setChosen(e.target.value)} aria-label="Memory">
            <optgroup label="This project">
              <option value="shared">{IMPLICIT.shared.label}</option>
              <option value="own">An agent&apos;s own memory</option>
              {named.map((m) => (
                <option key={m.slug} value={`memory:${m.slug}`}>
                  {m.display_name}
                </option>
              ))}
            </optgroup>
            {global.length > 0 && (
              <optgroup label="Organisation (read-only here)">
                {global.map((m) => (
                  <option key={m.slug} value={`global:${m.slug}`}>
                    {m.display_name} — {reachLabel(m)}
                  </option>
                ))}
              </optgroup>
            )}
          </Select>
        </label>
        {chosen === "own" && (
          <label className="flex min-w-0 flex-col gap-1">
            <span className="text-xs text-ink-muted">Agent</span>
            <Select value={agent} onChange={(e) => setAgent(e.target.value)} aria-label="Agent">
              <option value="">—</option>
              {(agents.data ?? []).map((a) => (
                <option key={a.slug} value={a.slug}>
                  {a.display_name}
                </option>
              ))}
            </Select>
          </label>
        )}
        {canWrite && (
          <Button onClick={() => setOpen(true)}>New project memory</Button>
        )}
        {canWrite && <MemoryReimport slug={slug} />}
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
              <span className="text-xs text-ink-muted">Search the memory</span>
              <Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Rounding, incident, test convention…" className="flex-1" />
            </label>
            <Button type="submit" tone="primary">
              Search
            </Button>
          </form>
          <ul className="space-y-3">
            {(results.data ?? []).map((memory) => (
              <li key={memory.id} className="raised border border-line p-3">
                <p className={LEGENDE}>{memory.kind}</p>
                <p className="font-mono text-xs">{memory.subject}</p>
                <p className="mt-1 text-sm">{memory.content}</p>
                <p className="mt-1 text-xs text-ink-muted">
                  Valid from {shortDate(memory.valid_from)}
                  {memory.valid_to ? ` to ${shortDate(memory.valid_to)}` : " (in force)"} · source{" "}
                  {String((memory.provenance as Record<string, unknown>)?.source ?? "—")}
                </p>
              </li>
            ))}
          </ul>
          <EtatDeLecture lecture={results} quoi="the memory" />
          {submitted && results.data?.length === 0 && <Empty>No memory for “{submitted}”</Empty>}
        </Card>

        <Card title="Facts proposed by agents">
          {isGlobal ? (
            <p className="text-sm text-ink-muted">
              What agents propose to an organisation memory is decided by an administrator, in{" "}
              <Link href={`/memories/${globalSlug}`} className="underline">
                the organisation&apos;s registry
              </Link>
              .
            </p>
          ) : !ready ? (
            <p className="text-sm text-ink-muted">Choose an agent to read its own memory.</p>
          ) : (
            <>
              <ul className="space-y-3">
                {(pending.data ?? [])
                  .filter((memory) => memory.id)
                  .map((memory) => (
                    <li key={memory.id} className="rounded border border-line border-l-2 border-l-warn bg-surface p-3">
                      <p className="font-mono text-xs">{memory.subject}</p>
                      <p className="mt-1 text-sm">{memory.content}</p>
                      <p className="mt-1 text-xs text-ink-muted">Proposed by run {memory.proposed_by ?? "—"}</p>
                      <div className="mt-2 flex gap-2">
                        <Button tone="primary" onClick={() => decide(memory.id ?? "", "accept")}>
                          Accept
                        </Button>
                        <Button tone="danger" onClick={() => decide(memory.id ?? "", "reject")}>
                          Reject
                        </Button>
                      </div>
                    </li>
                  ))}
              </ul>
              <EtatDeLecture lecture={pending} quoi="the proposed facts" />
              {pending.data?.length === 0 && <Empty>No pending fact</Empty>}
            </>
          )}
        </Card>
      </div>
      <Drawer open={open} onClose={() => setOpen(false)} title="New project memory">
        <form
          className="space-y-3 text-sm"
          onSubmit={(event) => {
            event.preventDefault();
            void create();
          }}
        >
          <p className="text-ink-muted">Read by the runs of this project only.</p>
          <label className="block space-y-1">
            <span className="text-xs text-ink-muted">Slug</span>
            <Input required pattern="^[a-z][a-z0-9-]{1,62}$" value={form.slug} onChange={(e) => setForm({ ...form, slug: e.target.value })} placeholder="decisions" />
          </label>
          <label className="block space-y-1">
            <span className="text-xs text-ink-muted">Name</span>
            <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Decisions" />
          </label>
          <label className="block space-y-1">
            <span className="text-xs text-ink-muted">Description</span>
            <Input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          </label>
          <Button type="submit" tone="primary">
            Create
          </Button>
        </form>
      </Drawer>
    </div>
  );
}
