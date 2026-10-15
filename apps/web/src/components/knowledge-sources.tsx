// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { Badge, Card, ErrorNote, Input, Select, buttonClasses, type Tone } from "@/components/ui";
import { api } from "@/lib/api";
import { relative } from "@/lib/format";
import { sourceConfig, syncSummary } from "@/lib/knowledge";
import type { KnowledgeSource } from "@/lib/types";

type Kind = "sharepoint" | "web" | "git";
const TONE: Record<string, Tone> = { idle: "ok", syncing: "neutral", failed: "danger" };
const KIND: Record<Kind, string> = { sharepoint: "SharePoint / OneDrive", web: "web site", git: "git repository" };

/**
 * Where a knowledge base's documents are synced from (S33-04): SharePoint or OneDrive through the
 * organisation's `documents` connector (Graph, `Sites.Selected`), an https site, a git repository.
 * Each says when it last synced and what that did; a document deleted at the source leaves the base.
 */
export function KnowledgeSources({
  org,
  slug,
  sources,
  onChange,
}: {
  org: string;
  slug: string;
  sources: KnowledgeSource[];
  onChange: () => void;
}) {
  const [kind, setKind] = useState<Kind>("sharepoint");
  const [fields, setFields] = useState({ name: "", connector: "", site: "", folder: "", url: "", ref: "main", paths: "*.md", interval: "60" });
  const [error, setError] = useState<string | null>(null);
  // A SharePoint source picks one of the organisation's `documents/sharepoint` connectors: never typed.
  const connectors = useQuery({ queryKey: ["org-connectors", org], queryFn: () => api.orgConnectors(org) });
  const pickable = (connectors.data ?? []).filter((c) => c.kind === "documents" && c.type === "sharepoint");
  const set = (key: keyof typeof fields) => (e: { target: { value: string } }) => setFields({ ...fields, [key]: e.target.value });
  async function act(gesture: () => Promise<unknown>) {
    setError(null);
    try {
      await gesture();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "refused");
    }
    onChange();
  }
  return (
    <Card title="sources">
      <div className="space-y-3 text-sm">
        {sources.length === 0 ? (
          <p className="text-ink-muted">No source: uploads only.</p>
        ) : (
          <ul className="space-y-2" data-testid="knowledge-sources">
            {sources.map((source) => (
              <li key={source.id} className="flex flex-wrap items-baseline gap-2 border-t border-line pt-2">
                <strong>{source.name}</strong>
                <span className="text-xs text-ink-muted">{KIND[source.kind as Kind] ?? source.kind}</span>
                <Badge tone={TONE[source.status] ?? "neutral"}>{source.status}</Badge>
                <span className="text-xs">
                  {source.last_synced_at ? `synced ${relative(source.last_synced_at)}` : "not synced yet"} ·{" "}
                  {syncSummary(source.last_summary)} · {source.document_count ?? 0} document(s) · every {source.interval_minutes} min
                </span>
                {source.last_error && <span className="w-full text-xs text-danger">{source.last_error}</span>}
                <span className="ml-auto space-x-2 text-xs">
                  <button type="button" className="underline" onClick={() => void act(() => api.syncKnowledgeSource(org, slug, source.id))}>
                    sync now
                  </button>
                  <button type="button" className="underline" onClick={() => void act(() => api.deleteKnowledgeSource(org, slug, source.id))}>
                    remove
                  </button>
                </span>
              </li>
            ))}
          </ul>
        )}
        <form
          className="grid gap-2 sm:grid-cols-3"
          onSubmit={(event) => {
            event.preventDefault();
            void act(() =>
              api.createKnowledgeSource(org, slug, {
                kind,
                name: fields.name || KIND[kind],
                connector: kind === "sharepoint" ? fields.connector : null,
                config: sourceConfig(kind, fields),
                interval_minutes: Number(fields.interval) || 60,
              }),
            );
          }}
        >
          <Select aria-label="source kind" value={kind} onChange={(e) => setKind(e.target.value as Kind)}>
            <option value="sharepoint">SharePoint / OneDrive</option>
            <option value="web">web site (https, same host)</option>
            <option value="git">git repository (https)</option>
          </Select>
          <Input aria-label="source name" placeholder="name" value={fields.name} onChange={set("name")} />
          <Input aria-label="interval in minutes" placeholder="every … minutes" value={fields.interval} onChange={set("interval")} />
          {kind === "sharepoint" ? (
            <>
              {pickable.length > 0 || !connectors.data ? (
                <Select aria-label="documents connector" value={fields.connector} onChange={set("connector")}>
                  <option value="">documents connector…</option>
                  {pickable.map((c) => (
                    <option key={c.name} value={c.name}>
                      {c.name}
                    </option>
                  ))}
                </Select>
              ) : (
                <p className="text-xs text-ink-muted" data-testid="no-documents-connector">
                  No SharePoint connector in this organisation.{" "}
                  <Link className="underline" href="/admin/connectors?type=documents/sharepoint">
                    declare a SharePoint connector
                  </Link>
                </p>
              )}
              <Input aria-label="site or drive id" placeholder="site id, or drive id (b!…)" value={fields.site} onChange={set("site")} />
              <Input aria-label="folder" placeholder="folder (optional)" value={fields.folder} onChange={set("folder")} />
            </>
          ) : (
            <Input aria-label="url" placeholder="https://…" value={fields.url} onChange={set("url")} className="sm:col-span-2" />
          )}
          {kind === "git" && (
            <>
              <Input aria-label="ref" placeholder="branch or commit" value={fields.ref} onChange={set("ref")} />
              <Input aria-label="paths" placeholder="docs/*.md, *.md" value={fields.paths} onChange={set("paths")} />
            </>
          )}
          <div>
            <button type="submit" className={buttonClasses("secondary", "sm")}>
              add source
            </button>
          </div>
        </form>
        <p className="text-xs text-ink-muted">
          SharePoint reads with <code>Sites.Selected</code> only.
        </p>
        {error && <ErrorNote>{error}</ErrorNote>}
      </div>
    </Card>
  );
}
