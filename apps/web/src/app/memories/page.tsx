// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { Button, Card, Drawer, Empty, ErrorNote, Eyebrow, Heading, Input, Select } from "@/components/ui";
import { api } from "@/lib/api";
import { reachLabel } from "@/lib/memories";
import { useSession } from "@/lib/session";
import type { MemoryStoreCreate } from "@/lib/types";

/**
 * The organisation's global memories (S40-11, ADR 0075): facts learned from work that every project of
 * their reach may read. An organisation administrator curates them; what agents write to one is a
 * proposal waiting for an administrator. A project's own memories are in the project.
 */
export default function MemoriesPage() {
  const { org, me } = useSession();
  const client = useQueryClient();
  const memories = useQuery({ queryKey: ["memories", org, "global"], queryFn: () => api.memories(org, { scope: "global" }) });
  const projects = useQuery({ queryKey: ["projects", org], queryFn: () => api.projects(org) });
  const admin = (me?.memberships ?? []).some((m) => m.org === org && !m.project_slug && m.role === "org_admin");
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<{ slug: string; name: string; description: string; reach: "org" | "projects"; chosen: string[] }>({
    slug: "",
    name: "",
    description: "",
    reach: "org",
    chosen: [],
  });
  const [error, setError] = useState<string | null>(null);

  async function create() {
    setError(null);
    const body: MemoryStoreCreate = {
      slug: form.slug,
      display_name: form.name || form.slug,
      description: form.description || undefined,
      reach: form.reach,
      projects: form.reach === "projects" ? form.chosen : [],
    };
    try {
      await api.createGlobalMemory(org, body);
      await client.invalidateQueries({ queryKey: ["memories", org] });
      setOpen(false);
      setForm({ slug: "", name: "", description: "", reach: "org", chosen: [] });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Creation refused");
    }
  }

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <Eyebrow>What agents learn</Eyebrow>
        <Heading as="h1" size="xl">
          Memories
        </Heading>
        <p className="max-w-3xl text-sm text-ink-muted">
          Facts learned from work, shared across projects. An agent binds the ones it reads, in order, and at most one it writes
          to; what it writes here is a proposal an administrator accepts. A project&apos;s own memories are in the project.
        </p>
      </div>
      <Card
        title="Organisation memories"
        action={
          admin ? (
            <Button tone="primary" onClick={() => setOpen(true)}>
              New memory
            </Button>
          ) : undefined
        }
      >
        {memories.error ? (
          <ErrorNote>{String(memories.error)}</ErrorNote>
        ) : !memories.data ? (
          <p className="text-sm text-ink-muted">Reading the memories…</p>
        ) : memories.data.length === 0 ? (
          <Empty title="No organisation memory yet">
            {admin ? "Create one: agents then bind it, and projects within its reach read it." : "An organisation administrator creates them."}
          </Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="memories">
              <thead className="text-left text-xs text-ink-muted">
                <tr>
                  <th className="py-1 font-normal">Memory</th>
                  <th className="py-1 font-normal">Reach</th>
                  <th className="py-1 font-normal">Status</th>
                  <th className="py-1 font-normal">Used by</th>
                </tr>
              </thead>
              <tbody>
                {memories.data.map((memory) => (
                  <tr key={memory.slug} className={`border-t border-line ${memory.status === "archived" ? "opacity-60" : ""}`}>
                    <td className="py-2">
                      <Link href={`/memories/${memory.slug}`} className="font-medium hover:underline">
                        {memory.display_name}
                      </Link>
                      <p className="font-mono text-xs text-ink-muted">{memory.slug}</p>
                    </td>
                    <td className="text-xs">{reachLabel(memory)}</td>
                    <td className="text-xs">{memory.status ?? "active"}</td>
                    <td className="text-xs">
                      {(memory.used_by ?? []).length === 0 ? (
                        <span className="text-ink-muted">no agent</span>
                      ) : (
                        <span className="flex flex-wrap gap-x-2">
                          {(memory.used_by ?? []).map((u) => (
                            <Link key={u.agent} href={`/agents/${u.agent}`} className="underline">
                              {u.agent} v{u.version}
                            </Link>
                          ))}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <Drawer open={open} onClose={() => setOpen(false)} title="New organisation memory">
        <form
          className="space-y-3 text-sm"
          onSubmit={(event) => {
            event.preventDefault();
            void create();
          }}
        >
          <label className="block space-y-1">
            <span className="text-xs text-ink-muted">Slug</span>
            <Input required pattern="^[a-z][a-z0-9-]{1,62}$" value={form.slug} onChange={(e) => setForm({ ...form, slug: e.target.value })} placeholder="security-lessons" />
          </label>
          <label className="block space-y-1">
            <span className="text-xs text-ink-muted">Name</span>
            <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Security lessons" />
          </label>
          <label className="block space-y-1">
            <span className="text-xs text-ink-muted">Description</span>
            <Input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          </label>
          <label className="block space-y-1">
            <span className="text-xs text-ink-muted">Reach</span>
            <Select value={form.reach} onChange={(e) => setForm({ ...form, reach: e.target.value as typeof form.reach })}>
              <option value="org">Every project</option>
              <option value="projects">Some projects</option>
            </Select>
          </label>
          {form.reach === "projects" && (
            <fieldset className="space-y-1">
              <legend className="text-xs text-ink-muted">Projects that read it</legend>
              {(projects.data?.items ?? []).map((p) => (
                <label key={p.slug} className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={form.chosen.includes(p.slug)}
                    onChange={(e) =>
                      setForm({ ...form, chosen: e.target.checked ? [...form.chosen, p.slug] : form.chosen.filter((s) => s !== p.slug) })
                    }
                  />
                  {p.name ?? p.slug}
                </label>
              ))}
            </fieldset>
          )}
          <Button type="submit" tone="primary">
            Create
          </Button>
          {error && <ErrorNote>{error}</ErrorNote>}
        </form>
      </Drawer>
    </div>
  );
}
