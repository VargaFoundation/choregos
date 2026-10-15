// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import {
  Card,
  Empty,
  ErrorNote,
  Eyebrow,
  Heading,
  Input,
  Select,
  buttonClasses,
} from "@/components/ui";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";
import { statusSummary, usedByLabel } from "@/lib/knowledge";
import type { KnowledgeBaseCreate } from "@/lib/types";

const FILTERS = [
  { id: "all", label: "All" },
  { id: "org", label: "Organisation-wide" },
  { id: "projects", label: "Project" },
  { id: "groups", label: "Group" },
] as const;
type Filter = (typeof FILTERS)[number]["id"];

/** The `?scope=` of the address, when it names a filter. */
function filterFromUrl(): Filter {
  if (typeof window === "undefined") return "all";
  const wanted = new URLSearchParams(window.location.search).get("scope");
  return FILTERS.some((f) => f.id === wanted) ? (wanted as Filter) : "all";
}

/**
 * Knowledge bases (ADR 0051): a corpus a human curates — an architecture framework, a security
 * policy — converted to markdown and served to agents read-only. Not the memory, which holds what
 * agents learned from work.
 */
export default function KnowledgePage() {
  const { org } = useSession();
  const client = useQueryClient();
  const bases = useQuery({
    queryKey: ["knowledge", org],
    queryFn: () => api.knowledgeBases(org),
  });
  const projects = useQuery({
    queryKey: ["projects", org],
    queryFn: () => api.projects(org),
  });
  const projectNames = Object.fromEntries(
    (projects.data?.items ?? []).map((p) => [p.slug, p.name]),
  );
  const [filter, setFilter] = useState<Filter>(filterFromUrl);
  function pick(next: Filter) {
    setFilter(next);
    const url = new URL(window.location.href);
    if (next === "all") url.searchParams.delete("scope");
    else url.searchParams.set("scope", next);
    window.history.replaceState(null, "", url);
  }
  const shown = (bases.data ?? []).filter(
    (b) => filter === "all" || b.scope === filter,
  );
  const [form, setForm] = useState<{
    slug: string;
    name: string;
    scope: "org" | "projects" | "groups";
    members: string;
    chosen: string[];
    serving_mode: "files" | "index";
  }>({
    slug: "",
    name: "",
    scope: "org",
    members: "",
    chosen: [],
    serving_mode: "files",
  });
  const [error, setError] = useState<string | null>(null);
  async function create() {
    setError(null);
    const members = form.members
      .split(",")
      .map((m) => m.trim())
      .filter(Boolean);
    const body: KnowledgeBaseCreate = {
      slug: form.slug,
      name: form.name || form.slug,
      scope: form.scope,
      serving_mode: form.serving_mode,
      projects: form.scope === "projects" ? form.chosen : [],
      groups: form.scope === "groups" ? members : [],
    };
    try {
      await api.createKnowledgeBase(org, body);
      setForm({
        slug: "",
        name: "",
        scope: "org",
        members: "",
        chosen: [],
        serving_mode: "files",
      });
      await client.invalidateQueries({ queryKey: ["knowledge", org] });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Creation refused");
    }
  }
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <Eyebrow>What agents read</Eyebrow>
        <Heading as="h1" size="xl">
          Knowledge
        </Heading>
        <p className="max-w-3xl text-sm text-ink-muted">
          Documents you choose, converted to markdown. Agents read them, never
          write them: mounted as <em>files</em>, or searched in an{" "}
          <em>index</em> with citations.
        </p>
      </div>
      <Card title="Knowledge bases">
        {bases.error ? (
          <ErrorNote>{String(bases.error)}</ErrorNote>
        ) : !bases.data ? (
          <p className="text-sm text-ink-muted">reading the knowledge bases…</p>
        ) : bases.data.length === 0 ? (
          <Empty title="No knowledge base yet">
            Create one below, then upload its documents.
          </Empty>
        ) : (
          <div className="space-y-3">
            <div
              role="group"
              aria-label="Filter by scope"
              className="flex flex-wrap gap-2"
            >
              {FILTERS.map((f) => (
                <button
                  key={f.id}
                  type="button"
                  aria-pressed={filter === f.id}
                  onClick={() => pick(f.id)}
                  className={buttonClasses(
                    filter === f.id ? "primary" : "ghost",
                    "sm",
                  )}
                >
                  {f.label}
                </button>
              ))}
            </div>
            {shown.length === 0 && (
              <p className="text-sm text-ink-muted">
                No knowledge base with this scope.
              </p>
            )}
            <div className="overflow-x-auto">
              <table className="w-full text-sm" data-testid="knowledge-bases">
                <thead className="text-left text-xs text-ink-muted">
                  <tr>
                    <th className="py-1 font-normal">Base</th>
                    <th className="py-1 font-normal">Used by</th>
                    <th className="py-1 font-normal">Served as</th>
                    <th className="py-1 font-normal">Documents</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((base) => (
                    <tr key={base.slug} className="border-t border-line">
                      <td className="py-2">
                        <Link
                          href={`/knowledge/${base.slug}`}
                          className="font-medium hover:underline"
                        >
                          {base.name}
                        </Link>
                        <p className="font-mono text-xs text-ink-muted">
                          {base.slug}
                          {base.status !== "active" && ` · ${base.status}`}
                        </p>
                      </td>
                      <td className="text-xs">
                        {base.scope === "projects" ? (
                          <span className="flex flex-wrap gap-x-2">
                            {(base.projects ?? []).map((p) => (
                              <Link
                                key={p}
                                href={`/p/${encodeURIComponent(p)}/knowledge`}
                                className="underline"
                              >
                                {projectNames[p] ?? p}
                              </Link>
                            ))}
                          </span>
                        ) : (
                          usedByLabel(base)
                        )}
                      </td>
                      <td className="text-xs">{base.serving_mode}</td>
                      <td className="text-xs">
                        {statusSummary(base.documents_by_status)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </Card>
      <Card title="Create a knowledge base">
        <form
          className="grid gap-3 text-sm sm:grid-cols-2"
          onSubmit={(event) => {
            event.preventDefault();
            void create();
          }}
        >
          <label className="space-y-1">
            <span className="text-xs text-ink-muted">Slug</span>
            <Input
              required
              pattern="^[a-z][a-z0-9-]{1,62}$"
              value={form.slug}
              onChange={(e) => setForm({ ...form, slug: e.target.value })}
              placeholder="architecture-framework"
            />
          </label>
          <label className="space-y-1">
            <span className="text-xs text-ink-muted">Name</span>
            <Input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="Architecture framework"
            />
          </label>
          <label className="space-y-1">
            <span className="text-xs text-ink-muted">Scope</span>
            <Select
              value={form.scope}
              onChange={(e) =>
                setForm({ ...form, scope: e.target.value as typeof form.scope })
              }
            >
              <option value="org">Whole organisation</option>
              <option value="projects">Some projects</option>
              <option value="groups">
                Some groups (people only, never a run)
              </option>
            </Select>
          </label>
          {form.scope === "projects" && (
            <fieldset className="space-y-1">
              <legend className="text-xs text-ink-muted">
                Projects served
              </legend>
              {(projects.data?.items ?? []).map((p) => (
                <label key={p.slug} className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={form.chosen.includes(p.slug)}
                    onChange={(e) =>
                      setForm({
                        ...form,
                        chosen: e.target.checked
                          ? [...form.chosen, p.slug]
                          : form.chosen.filter((c) => c !== p.slug),
                      })
                    }
                  />
                  <span>
                    {p.name}{" "}
                    <span className="font-mono text-xs text-ink-muted">
                      {p.slug}
                    </span>
                  </span>
                </label>
              ))}
            </fieldset>
          )}
          {form.scope === "groups" && (
            <label className="space-y-1">
              <span className="text-xs text-ink-muted">
                IdP groups, comma-separated
              </span>
              <Input
                required
                value={form.members}
                onChange={(e) => setForm({ ...form, members: e.target.value })}
              />
            </label>
          )}
          <label className="space-y-1">
            <span className="text-xs text-ink-muted">Served as</span>
            <Select
              value={form.serving_mode}
              onChange={(e) =>
                setForm({
                  ...form,
                  serving_mode: e.target.value as typeof form.serving_mode,
                })
              }
            >
              <option value="files">Files — mounted in the workspace</option>
              <option value="index">Index — searched, with citations</option>
            </Select>
          </label>
          <p className="text-xs text-ink-muted sm:col-span-2">
            To give one project its own knowledge, create it from the
            project&apos;s Knowledge page.
          </p>
          <div className="flex items-end">
            <button
              type="submit"
              disabled={form.scope === "projects" && form.chosen.length === 0}
              className={buttonClasses("primary")}
            >
              Create
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
