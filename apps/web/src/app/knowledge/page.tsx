// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { Card, Empty, ErrorNote, Eyebrow, Heading, Input, Select, buttonClasses } from "@/components/ui";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";
import { scopeLabel, statusSummary } from "@/lib/knowledge";
import type { KnowledgeBaseCreate } from "@/lib/types";

/**
 * Knowledge bases (ADR 0051): a corpus a human curates — an architecture framework, a security
 * policy — converted to markdown and served to agents read-only. Not the memory, which holds what
 * agents learned from work.
 */
export default function KnowledgePage() {
  const { org } = useSession();
  const client = useQueryClient();
  const bases = useQuery({ queryKey: ["knowledge", org], queryFn: () => api.knowledgeBases(org) });
  const [form, setForm] = useState<{ slug: string; name: string; scope: "org" | "projects" | "groups"; members: string; serving_mode: "files" | "index" }>(
    { slug: "", name: "", scope: "org", members: "", serving_mode: "files" },
  );
  const [error, setError] = useState<string | null>(null);
  async function create() {
    setError(null);
    const members = form.members.split(",").map((m) => m.trim()).filter(Boolean);
    const body: KnowledgeBaseCreate = {
      slug: form.slug,
      name: form.name || form.slug,
      scope: form.scope,
      serving_mode: form.serving_mode,
      projects: form.scope === "projects" ? members : [],
      groups: form.scope === "groups" ? members : [],
    };
    try {
      await api.createKnowledgeBase(org, body);
      setForm({ slug: "", name: "", scope: "org", members: "", serving_mode: "files" });
      await client.invalidateQueries({ queryKey: ["knowledge", org] });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "creation refused");
    }
  }
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <Eyebrow>what agents read</Eyebrow>
        <Heading as="h1" size="xl">
          knowledge
        </Heading>
        <p className="max-w-3xl text-sm text-ink-muted">
          A knowledge base is a corpus you choose — a framework, a policy, runbooks — uploaded as Word, PowerPoint, Excel,
          PDF, HTML or markdown, and converted to markdown. Agents read it, never write it. In <em>files</em> mode the
          corpus is mounted in the agent&apos;s workspace; in <em>index</em> mode the agent searches it and cites what it
          used.
        </p>
      </div>
      <Card title="knowledge bases">
        {bases.error ? (
          <ErrorNote>{String(bases.error)}</ErrorNote>
        ) : !bases.data ? (
          <p className="text-sm text-ink-muted">reading the knowledge bases…</p>
        ) : bases.data.length === 0 ? (
          <Empty title="no knowledge base yet">Create one below, then upload its documents.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="knowledge-bases">
              <thead className="text-left text-xs text-ink-muted">
                <tr>
                  <th className="py-1 font-normal">base</th>
                  <th className="py-1 font-normal">scope</th>
                  <th className="py-1 font-normal">served as</th>
                  <th className="py-1 font-normal">documents</th>
                </tr>
              </thead>
              <tbody>
                {bases.data.map((base) => (
                  <tr key={base.slug} className="border-t border-line">
                    <td className="py-2">
                      <Link href={`/knowledge/${base.slug}`} className="font-medium hover:underline">
                        {base.name}
                      </Link>
                      <p className="font-mono text-xs text-ink-muted">
                        {base.slug}
                        {base.status !== "active" && ` · ${base.status}`}
                      </p>
                    </td>
                    <td className="text-xs">{scopeLabel(base)}</td>
                    <td className="text-xs">{base.serving_mode}</td>
                    <td className="text-xs">{statusSummary(base.documents_by_status)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <Card title="create a knowledge base">
        <form
          className="grid gap-3 text-sm sm:grid-cols-2"
          onSubmit={(event) => {
            event.preventDefault();
            void create();
          }}
        >
          <label className="space-y-1">
            <span className="text-xs text-ink-muted">slug</span>
            <Input required pattern="^[a-z][a-z0-9-]{1,62}$" value={form.slug} onChange={(e) => setForm({ ...form, slug: e.target.value })} placeholder="architecture-framework" />
          </label>
          <label className="space-y-1">
            <span className="text-xs text-ink-muted">name</span>
            <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Architecture framework" />
          </label>
          <label className="space-y-1">
            <span className="text-xs text-ink-muted">scope</span>
            <Select value={form.scope} onChange={(e) => setForm({ ...form, scope: e.target.value as typeof form.scope })}>
              <option value="org">whole organisation</option>
              <option value="projects">some projects</option>
              <option value="groups">some groups (people only, never a run)</option>
            </Select>
          </label>
          {form.scope !== "org" && (
            <label className="space-y-1">
              <span className="text-xs text-ink-muted">{form.scope === "projects" ? "project slugs" : "IdP groups"}, comma-separated</span>
              <Input required value={form.members} onChange={(e) => setForm({ ...form, members: e.target.value })} />
            </label>
          )}
          <label className="space-y-1">
            <span className="text-xs text-ink-muted">served as</span>
            <Select value={form.serving_mode} onChange={(e) => setForm({ ...form, serving_mode: e.target.value as typeof form.serving_mode })}>
              <option value="files">files — mounted in the workspace</option>
              <option value="index">index — searched, with citations</option>
            </Select>
          </label>
          <div className="flex items-end">
            <button type="submit" className={buttonClasses("primary")}>
              create
            </button>
          </div>
          {error && (
            <div className="sm:col-span-2">
              <ErrorNote>{error}</ErrorNote>
            </div>
          )}
        </form>
      </Card>
    </div>
  );
}
