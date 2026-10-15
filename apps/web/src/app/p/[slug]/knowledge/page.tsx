// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useState } from "react";
import { Button, Card, Drawer, Empty, ErrorNote, Input, Select } from "@/components/ui";
import { api } from "@/lib/api";
import { ownedByProject, servedToProject, statusSummary } from "@/lib/knowledge";
import { useSession } from "@/lib/session";
import type { KnowledgeBase } from "@/lib/types";

/**
 * A project's knowledge (S39-14): what its runs are served — the bases scoped to it, then the
 * organisation-wide ones, read-only from here. A base created here is scoped to this project by
 * construction: there is no scope to choose. The organisation's registry stays at `/knowledge`.
 */
export default function ProjectKnowledgePage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const { me, org } = useSession();
  const router = useRouter();
  const client = useQueryClient();
  const project = useQuery({ queryKey: ["project", slug], queryFn: () => api.project(slug) });
  // Same key as the registry and the agent builder: one request for all three.
  const bases = useQuery({ queryKey: ["knowledge", org], queryFn: () => api.knowledgeBases(org) });
  const orgAdmin = (me?.memberships ?? []).some((m) => m.org === org && !m.project_slug && m.role === "org_admin");
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<{ slug: string; name: string; description: string; serving_mode: "files" | "index" }>({
    slug: "",
    name: "",
    description: "",
    serving_mode: "files",
  });
  const [error, setError] = useState<string | null>(null);
  const name = project.data?.name ?? slug;

  async function create() {
    setError(null);
    try {
      const created = await api.createKnowledgeBase(org, {
        slug: form.slug,
        name: form.name || form.slug,
        description: form.description || undefined,
        scope: "projects",
        projects: [slug],
        groups: [],
        serving_mode: form.serving_mode,
      });
      await client.invalidateQueries({ queryKey: ["knowledge", org] });
      setOpen(false);
      router.push(`/p/${encodeURIComponent(slug)}/knowledge/${created?.slug ?? form.slug}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Creation refused");
    }
  }

  const served = (bases.data ?? []).filter((b) => servedToProject(b, slug));
  const own = served.filter((b) => ownedByProject(b, slug));
  const shared = served.filter((b) => b.scope === "org");
  return (
    <div className="space-y-6">
      <p className="max-w-3xl text-sm text-ink-muted">
        The documents the runs of {name} read, never write. Bases for groups of people are not listed here: a run is never
        served one.
      </p>
      <Card
        title="This project's knowledge bases"
        action={
          orgAdmin ? (
            <Button tone="primary" onClick={() => setOpen(true)}>
              New knowledge base
            </Button>
          ) : undefined
        }
      >
        {bases.error ? (
          <ErrorNote>{String(bases.error)}</ErrorNote>
        ) : !bases.data ? (
          <p className="text-sm text-ink-muted">Reading the knowledge bases…</p>
        ) : own.length === 0 ? (
          <Empty title="No knowledge base of its own yet">
            {orgAdmin
              ? "Create one: it is served to the runs of this project only."
              : "Ask an organisation administrator to create one for this project."}
          </Empty>
        ) : (
          <BaseTable bases={own} slug={slug} testId="project-knowledge-own" />
        )}
      </Card>
      <Card title="Shared with every project">
        {!bases.data ? (
          <p className="text-sm text-ink-muted">Reading the knowledge bases…</p>
        ) : shared.length === 0 ? (
          <Empty title="Nothing shared">The organisation has no organisation-wide knowledge base.</Empty>
        ) : (
          <BaseTable bases={shared} slug={slug} testId="project-knowledge-shared" />
        )}
        <p className="mt-3 text-xs text-ink-muted">
          Curated by the organisation: see the <Link href="/knowledge" className="underline">organisation&apos;s registry</Link>.
        </p>
      </Card>
      <Drawer open={open} onClose={() => setOpen(false)} title="New knowledge base">
        <form
          className="space-y-3 text-sm"
          onSubmit={(event) => {
            event.preventDefault();
            void create();
          }}
        >
          <p className="text-ink-muted">Served to the runs of {name} only.</p>
          <label className="block space-y-1">
            <span className="text-xs text-ink-muted">Slug</span>
            <Input required pattern="^[a-z][a-z0-9-]{1,62}$" value={form.slug} onChange={(e) => setForm({ ...form, slug: e.target.value })} placeholder="runbooks" />
          </label>
          <label className="block space-y-1">
            <span className="text-xs text-ink-muted">Name</span>
            <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Runbooks" />
          </label>
          <label className="block space-y-1">
            <span className="text-xs text-ink-muted">Description</span>
            <Input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          </label>
          <label className="block space-y-1">
            <span className="text-xs text-ink-muted">Served as</span>
            <Select value={form.serving_mode} onChange={(e) => setForm({ ...form, serving_mode: e.target.value as typeof form.serving_mode })}>
              <option value="files">Files — mounted in the workspace</option>
              <option value="index">Index — searched, with citations</option>
            </Select>
          </label>
          <Button type="submit" tone="primary">
            Create
          </Button>
          {error && <ErrorNote>{error}</ErrorNote>}
        </form>
      </Drawer>
    </div>
  );
}

function BaseTable({ bases, slug, testId }: { bases: KnowledgeBase[]; slug: string; testId: string }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm" data-testid={testId}>
        <thead className="text-left text-xs text-ink-muted">
          <tr>
            <th className="py-1 font-normal">Base</th>
            <th className="py-1 font-normal">Served as</th>
            <th className="py-1 font-normal">Documents</th>
          </tr>
        </thead>
        <tbody>
          {bases.map((base) => (
            <tr key={base.slug} className={`border-t border-line ${base.status !== "active" ? "opacity-60" : ""}`}>
              <td className="py-2">
                <Link href={`/p/${encodeURIComponent(slug)}/knowledge/${base.slug}`} className="font-medium hover:underline">
                  {base.name}
                </Link>
                <p className="font-mono text-xs text-ink-muted">
                  {base.slug}
                  {base.status !== "active" && ` · ${base.status}`}
                </p>
              </td>
              <td className="text-xs">{base.serving_mode}</td>
              <td className="text-xs">{statusSummary(base.documents_by_status)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
