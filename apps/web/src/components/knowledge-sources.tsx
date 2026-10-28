// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState, type ReactNode } from "react";
import { ProcedurePanel } from "@/components/procedures/procedure-panel";
import { egressFlag, useEgressGuard } from "@/components/egress/egress-provider";
import { Badge, Card, Disclosure, ErrorNote, Input, LEGENDE, Select, Textarea, buttonClasses, type Tone } from "@/components/ui";
import { connectorTarget, sourceTarget } from "@/lib/procedures";
import { api } from "@/lib/api";
import { relative } from "@/lib/format";
import { RefreshFields } from "@/components/knowledge-refresh";
import {
  CREDENTIAL_HEADER,
  draftOf,
  headersOf,
  nextSyncLabel,
  refreshBody,
  refreshLabel,
  sourceConfig,
  syncSummary,
  truncationsOf,
  type RefreshDraft,
} from "@/lib/knowledge";
import type { KnowledgeSource } from "@/lib/types";

type Kind = "sharepoint" | "confluence" | "web" | "git";
const CONNECTED: Kind[] = ["sharepoint", "confluence"];
const TONE: Record<string, Tone> = { idle: "ok", syncing: "neutral", failed: "danger" };
const KIND: Record<Kind, string> = { sharepoint: "SharePoint / OneDrive", confluence: "Confluence space or page tree", web: "web site", git: "git repository" };
/** Kinds that read anonymously, or through a connector holding a credential when the material is private. */
const OPTIONALLY_CONNECTED: Kind[] = ["web", "git"];

/** A control with its name above it, for the fields a placeholder alone would leave unnamed once filled. */
function Labelled({ label, className, children }: { label: string; className?: string; children: ReactNode }) {
  return (
    <label className={`flex flex-col gap-1 ${className ?? ""}`}>
      <span className={LEGENDE}>{label}</span>
      {children}
    </label>
  );
}

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
  const [fields, setFields] = useState({ name: "", connector: "", site: "", folder: "", space: "", pageId: "", url: "", exclude: "", depth: "2", maxPages: "50", ref: "main", paths: "*.md", headers: "" });
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
                    connector: CONNECTED.includes(kind) ? fields.connector : OPTIONALLY_CONNECTED.includes(kind) ? fields.connector.trim() || null : null,
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
              // A connector is of one kind: the one picked for another kind does not carry over.
              setFields({ ...fields, connector: "" });
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
          <Labelled label="Name">
            <Input aria-label="source name" placeholder={KIND[kind]} value={fields.name} onChange={set("name")} />
          </Labelled>
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
            <Labelled label={kind === "web" ? "Start address" : "Repository address"} className="sm:col-span-2">
              <Input aria-label="url" placeholder="https://…" value={fields.url} onChange={set("url")} />
            </Labelled>
          )}
          {kind === "web" && (
            <>
              <label className="flex items-center gap-2 text-xs sm:col-span-3">
                <input type="checkbox" checked={pageOnly} onChange={(e) => setPageOnly(e.target.checked)} />
                This page only (no links followed)
              </label>
              {!pageOnly && (
                <>
                  <Labelled label="Link depth (0–5)">
                    <Input aria-label="crawl depth" type="number" min={0} max={5} value={fields.depth} onChange={set("depth")} />
                  </Labelled>
                  <Labelled label="Pages at most (1–500)">
                    <Input aria-label="max pages" type="number" min={1} max={500} value={fields.maxPages} onChange={set("maxPages")} />
                  </Labelled>
                </>
              )}
              <label className="flex items-center gap-2 text-xs sm:col-span-3">
                <input type="checkbox" checked={documents} onChange={(e) => setDocuments(e.target.checked)} />
                Include linked documents (PDF, Word, PowerPoint, Excel)
              </label>
              <Labelled label="Excluded paths" className="sm:col-span-2">
                <Input aria-label="excluded paths" placeholder="/es/*, /pt/*" value={fields.exclude} onChange={set("exclude")} />
              </Labelled>
              <Disclosure
                className="sm:col-span-3"
                title="Advanced: authentication and HTTP headers"
                summary={fields.connector ? `signed in through ${fields.connector}` : "anonymous"}
              >
                <WebAccess
                  connectors={pickable}
                  loaded={Boolean(connectors.data)}
                  connector={fields.connector}
                  onConnector={set("connector")}
                  headers={fields.headers}
                  onHeaders={set("headers")}
                />
              </Disclosure>
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
          <RefreshFields value={refresh} onChange={setRefresh} />
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

/**
 * How a web source reaches a site behind a login (S47-48): through a `documents/web` connector, which
 * holds the credential as a secret and is pinned to the site's host, plus plain headers of the source's
 * own (`Accept-Language`). A header that looks like a credential is flagged here and refused by the API:
 * it would be stored in the clear.
 */
function WebAccess({
  connectors,
  loaded,
  connector,
  onConnector,
  headers,
  onHeaders,
}: {
  connectors: { name: string }[];
  loaded: boolean;
  connector: string;
  onConnector: (e: { target: { value: string } }) => void;
  headers: string;
  onHeaders: (e: { target: { value: string } }) => void;
}) {
  const secretLike = Object.keys(headersOf(headers)).filter((name) => CREDENTIAL_HEADER.test(name));
  return (
    <div className="space-y-3 text-sm">
      <div className="flex flex-wrap items-end gap-2">
        <Labelled label="Authentication" className="min-w-[16rem]">
          <Select aria-label="web connector" value={connector} onChange={onConnector}>
            <option value="">anonymous (public site)</option>
            {connectors.map((c) => (
              <option key={c.name} value={c.name}>
                {c.name}
              </option>
            ))}
          </Select>
        </Labelled>
        <ProcedurePanel target={connectorTarget("documents", "web")} label="Set up a login connector" autoOpen={false} />
      </div>
      <p className="text-xs text-ink-muted">
        A site behind a login is read with a <strong>bearer token</strong>, <strong>HTTP Basic</strong> or a{" "}
        <strong>named header</strong> (an API key, a session cookie). An organisation administrator declares it once as a{" "}
        <code>documents/web</code> connector, where it is a secret pinned to the site&apos;s host: it is never sent anywhere else.
        {loaded && connectors.length === 0 && (
          <>
            {" "}
            <Link className="underline" href="/admin/connectors?type=documents/web" data-testid="no-web-connector">
              declare a login connector
            </Link>
          </>
        )}
      </p>
      <Labelled label="Extra HTTP headers, one per line">
        <Textarea
          aria-label="http headers"
          className="min-h-16 font-mono text-xs"
          placeholder={"Accept-Language: fr\nX-Requested-With: choregos"}
          value={headers}
          onChange={onHeaders}
        />
      </Labelled>
      {secretLike.length > 0 && (
        <p className="text-xs text-danger" data-testid="secret-header">
          {secretLike.join(", ")} looks like a credential: it would be stored in the clear, so the API refuses it here. Put it in a
          login connector.
        </p>
      )}
    </div>
  );
}
