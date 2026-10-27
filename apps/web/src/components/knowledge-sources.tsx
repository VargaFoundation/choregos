// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { ProcedurePanel } from "@/components/procedures/procedure-panel";
import { egressFlag, useEgressGuard } from "@/components/egress/egress-provider";
import { Badge, Card, ErrorNote, Input, Select, buttonClasses, type Tone } from "@/components/ui";
import { connectorTarget, sourceTarget } from "@/lib/procedures";
import { api } from "@/lib/api";
import { relative } from "@/lib/format";
import { RefreshFields } from "@/components/knowledge-refresh";
import { draftOf, nextSyncLabel, refreshBody, refreshLabel, sourceConfig, syncSummary, truncationsOf, type RefreshDraft } from "@/lib/knowledge";
import type { KnowledgeSource } from "@/lib/types";

type Kind = "sharepoint" | "confluence" | "web" | "git";
const CONNECTED: Kind[] = ["sharepoint", "confluence"];
const TONE: Record<string, Tone> = { idle: "ok", syncing: "neutral", failed: "danger" };
const KIND: Record<Kind, string> = { sharepoint: "SharePoint / OneDrive", confluence: "Confluence space or page tree", web: "web site", git: "git repository" };

/**
 * Where a knowledge base's documents are synced from (S33-04): SharePoint or OneDrive through the
 * organisation's `documents` connector (Graph, `Sites.Selected`), a Confluence Cloud space or page tree (S39-20, same kind of connector), an https site, a git repository.
 * Each says when it last synced and what that did; a document deleted at the source leaves the base.
 */
export function KnowledgeSources({
  org,
  slug,
  sources,
  onChange,
  canEdit = true,
}: {
  org: string;
  slug: string;
  sources: KnowledgeSource[];
  onChange: () => void;
  /** `can.edit` of the base (S42-11): without it the sources are listed and nothing changes them. */
  canEdit?: boolean;
}) {
  const [kind, setKind] = useState<Kind>("sharepoint");
  // The procedure opens by itself once the person has chosen a kind, not when the page loads.
  const [chosen, setChosen] = useState(false);
  const [fields, setFields] = useState({ name: "", connector: "", site: "", folder: "", space: "", pageId: "", url: "", exclude: "", depth: "2", maxPages: "50", ref: "main", paths: "*.md" });
  const [refresh, setRefresh] = useState<RefreshDraft>(draftOf());
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState<RefreshDraft>(draftOf());
  const [applyAll, setApplyAll] = useState(false);
  const [attachments, setAttachments] = useState(false);
  const [pageOnly, setPageOnly] = useState(false);
  const [documents, setDocuments] = useState(false);
  const [subPages, setSubPages] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const guard = useEgressGuard();
  // A connected source picks one of the organisation's `documents/<kind>` connectors: never typed.
  const connectors = useQuery({ queryKey: ["org-connectors", org], queryFn: () => api.orgConnectors(org) });
  const pickable = (connectors.data ?? []).filter((c) => c.kind === "documents" && c.type === kind);
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
    <Card
      title="sources"
      action={
        sources.length > 0 && canEdit ? (
          <button type="button" className="text-xs underline" onClick={() => void act(() => api.syncKnowledgeBase(org, slug))}>
            sync all now
          </button>
        ) : undefined
      }
    >
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
                  {syncSummary(source.last_summary)} · {source.document_count ?? 0} document(s) · {refreshLabel(source)} · {nextSyncLabel(source)}
                </span>
                {source.last_error && <span className="w-full text-xs text-danger">{source.last_error}</span>}
                {truncationsOf(source.last_summary).length > 0 && (
                  <ul className="w-full text-xs text-warn" data-testid={`source-truncations-${source.id}`}>
                    {truncationsOf(source.last_summary).map((reason) => (
                      <li key={reason}>Cut: {reason}</li>
                    ))}
                  </ul>
                )}
                {canEdit && (
                <span className="ml-auto space-x-2 text-xs">
                  <button type="button" className="underline" onClick={() => void act(() => api.syncKnowledgeSource(org, slug, source.id))}>
                    sync now
                  </button>
                  <button
                    type="button"
                    className="underline"
                    onClick={() => {
                      setDraft(draftOf(source));
                      setApplyAll(false);
                      setEditing(editing === source.id ? null : source.id);
                    }}
                  >
                    edit refresh
                  </button>
                  <button type="button" className="underline" onClick={() => void act(() => api.deleteKnowledgeSource(org, slug, source.id))}>
                    remove
                  </button>
                </span>
                )}
                {editing === source.id && (
                  <form
                    className="grid w-full gap-2 sm:grid-cols-3"
                    aria-label="edit refresh"
                    onSubmit={(event) => {
                      event.preventDefault();
                      const targets = applyAll ? sources : [source];
                      void act(async () => {
                        for (const target of targets) await api.updateKnowledgeSource(org, slug, target.id, refreshBody(draft));
                        setEditing(null);
                      });
                    }}
                  >
                    <RefreshFields value={draft} onChange={setDraft} />
                    <label className="flex items-center gap-1 text-xs">
                      <input type="checkbox" checked={applyAll} onChange={(e) => setApplyAll(e.target.checked)} />
                      apply to all sources
                    </label>
                    <div className="space-x-2">
                      <button type="submit" className={buttonClasses("secondary", "sm")}>
                        save refresh
                      </button>
                      <button type="button" className="text-xs underline" onClick={() => setEditing(null)}>
                        cancel
                      </button>
                    </div>
                  </form>
                )}
              </li>
            ))}
          </ul>
        )}
        {canEdit && (
        <>
        <form
          className="grid gap-2 sm:grid-cols-3"
          onSubmit={(event) => {
            event.preventDefault();
            void act(() =>
              guard(
                (allow) =>
                  api.createKnowledgeSource(org, slug, {
                    kind,
                    name: fields.name || KIND[kind],
                    connector: CONNECTED.includes(kind) ? fields.connector : kind === "git" ? fields.connector.trim() || null : null,
                    config: sourceConfig(kind, { ...fields, attachments, pageOnly, documents, descendants: subPages }),
                    ...refreshBody(refresh),
                    ...egressFlag(allow),
                  }),
                {
                  gesture: `Add the ${KIND[kind]} source ${fields.name || KIND[kind]}`,
                  origin: `knowledge-source:${fields.name || KIND[kind]}`,
                  reason: `the ${KIND[kind]} knowledge source ${fields.name || KIND[kind]}`,
                },
              ),
            );
          }}
        >
          <Select
            aria-label="source kind"
            value={kind}
            onChange={(e) => {
              setKind(e.target.value as Kind);
              setChosen(true);
            }}
          >
            <option value="sharepoint">SharePoint / OneDrive</option>
            <option value="confluence">Confluence space or page tree</option>
            <option value="web">web site (https, same host)</option>
            <option value="git">git repository (https)</option>
          </Select>
          <div className="flex flex-wrap items-center gap-2 sm:col-span-3" data-testid="source-procedures">
            <ProcedurePanel target={sourceTarget(kind)} autoOpen={chosen} />
            {CONNECTED.includes(kind) && (
              <ProcedurePanel
                target={connectorTarget("documents", kind)}
                label="Set up the connector"
                autoOpen={false}
              />
            )}
          </div>
          <Input aria-label="source name" placeholder="name" value={fields.name} onChange={set("name")} />
          <RefreshFields value={refresh} onChange={setRefresh} />
          {CONNECTED.includes(kind) && (
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
                  No {kind === "confluence" ? "Confluence" : "SharePoint"} connector in this organisation.{" "}
                  <Link className="underline" href={`/admin/connectors?type=documents/${kind}`}>
                    declare a {kind === "confluence" ? "Confluence" : "SharePoint"} connector
                  </Link>
                </p>
              )}
            </>
          )}
          {kind === "sharepoint" ? (
            <>
              <Input aria-label="site or drive id" placeholder="site id, or drive id (b!…)" value={fields.site} onChange={set("site")} />
              <Input aria-label="folder" placeholder="folder (optional)" value={fields.folder} onChange={set("folder")} />
            </>
          ) : kind === "confluence" ? (
            <>
              <Input aria-label="space key" placeholder="space key (ENG)" value={fields.space} onChange={set("space")} />
              <Input aria-label="page ids" placeholder="page ids or page URLs, comma separated (optional, up to 50)" value={fields.pageId} onChange={set("pageId")} />
              <label className="flex items-center gap-2 text-xs">
                <input type="checkbox" checked={subPages} onChange={(e) => setSubPages(e.target.checked)} />
                and its sub-pages
              </label>
              <label className="flex items-center gap-2 text-xs">
                <input type="checkbox" checked={attachments} onChange={(e) => setAttachments(e.target.checked)} />
                include attachments (pdf, docx, pptx, xlsx, md, txt)
              </label>
            </>
          ) : (
            <Input aria-label="url" placeholder="https://…" value={fields.url} onChange={set("url")} className="sm:col-span-2" />
          )}
          {kind === "web" && (
            <>
              <label className="flex items-center gap-2 text-xs sm:col-span-3">
                <input type="checkbox" checked={pageOnly} onChange={(e) => setPageOnly(e.target.checked)} />
                This page only (no links followed)
              </label>
              {!pageOnly && (
                <>
                  <Input aria-label="crawl depth" type="number" min={0} max={5} placeholder="link depth (0-5)" value={fields.depth} onChange={set("depth")} />
                  <Input aria-label="max pages" type="number" min={1} max={500} placeholder="max pages (1-500)" value={fields.maxPages} onChange={set("maxPages")} />
                </>
              )}
              <label className="flex items-center gap-2 text-xs sm:col-span-3">
                <input type="checkbox" checked={documents} onChange={(e) => setDocuments(e.target.checked)} />
                Include linked documents (PDF, Word, PowerPoint, Excel)
              </label>
              <Input aria-label="excluded paths" placeholder="excluded paths, e.g. /es/*, /pt/*" value={fields.exclude} onChange={set("exclude")} className="sm:col-span-2" />
            </>
          )}
          {kind === "git" && (
            <>
              <Input aria-label="ref" placeholder="branch or commit" value={fields.ref} onChange={set("ref")} />
              <Input aria-label="paths" placeholder="docs/*.md, *.md" value={fields.paths} onChange={set("paths")} />
              <Select aria-label="git connector" value={fields.connector} onChange={set("connector")} className="sm:col-span-2">
                <option value="">public repository (no connector)</option>
                {pickable.map((c) => (
                  <option key={c.name} value={c.name}>
                    {c.name}
                  </option>
                ))}
              </Select>
            </>
          )}
          <div>
            <button type="submit" className={buttonClasses("secondary", "sm")}>
              add source
            </button>
          </div>
        </form>
        <p className="text-xs text-ink-muted">
          SharePoint reads with <code>Sites.Selected</code> only. A Confluence source mirrors everything the connector&apos;s account
          can read in the space: it becomes readable by everyone this base is open to. The page id is the number in the page&apos;s
          URL (<code>/pages/&lt;id&gt;/…</code>).
        </p>
        </>
        )}
        {error && <ErrorNote>{error}</ErrorNote>}
      </div>
    </Card>
  );
}
