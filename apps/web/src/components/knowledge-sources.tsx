// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState, type ReactNode } from "react";
import { BookOpen, Cloud, GitBranch, Globe, Plus, type LucideIcon } from "lucide-react";
import { ProcedurePanel } from "@/components/procedures/procedure-panel";
import { egressFlag, useEgressGuard } from "@/components/egress/egress-provider";
import { Badge, Card, Disclosure, ErrorNote, Input, LEGENDE, Select, Textarea, buttonClasses } from "@/components/ui";
import { connectorTarget, sourceTarget } from "@/lib/procedures";
import { api } from "@/lib/api";
import { relative } from "@/lib/format";
import { RefreshFields } from "@/components/knowledge-refresh";
import {
  CONNECTED_KINDS,
  CREDENTIAL_HEADER,
  draftOf,
  firstUsableKind,
  GITHUB_APP_HELP,
  GITHUB_APP_LABEL,
  githubAppOffered,
  headersOf,
  nextSyncLabel,
  refreshBody,
  refreshLabel,
  sourceConfig,
  sourceState,
  syncSummary,
  truncationsOf,
  type RefreshDraft,
  type SourceKind,
} from "@/lib/knowledge";
import { orgConnectorHref } from "@/lib/connector-link";
import { isOrgAdmin } from "@/lib/plugins";
import type { KnowledgeSource, OrgConnector } from "@/lib/types";

type Kind = SourceKind;
const CONNECTED: Kind[] = CONNECTED_KINDS;
const KIND: Record<Kind, string> = { sharepoint: "SharePoint / OneDrive", confluence: "Confluence space or page tree", web: "web site", git: "git repository" };
const KIND_OPTION: Record<Kind, string> = {
  sharepoint: "SharePoint / OneDrive",
  confluence: "Confluence space or page tree",
  web: "web site (https, same host)",
  git: "git repository (https)",
};
const KIND_ICON: Record<Kind, LucideIcon> = { sharepoint: Cloud, confluence: BookOpen, web: Globe, git: GitBranch };
/** Kinds that read anonymously, or through a connector holding a credential when the material is private. */
const OPTIONALLY_CONNECTED: Kind[] = ["web", "git"];
const EMPTY_FIELDS = { name: "", connector: "", site: "", folder: "", space: "", pageId: "", url: "", exclude: "", depth: "2", maxPages: "50", ref: "main", paths: "*.md", headers: "" };

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
 * The connector a source syncs through, next to it (owner review of 2026-10-28): a link to its card in
 * Administration → Installed connectors. A member who cannot read the organisation's connectors still
 * sees its name; one that no longer exists says so; a source without one says what it reads instead.
 */
function SourceConnector({
  source,
  known,
  unreadable,
}: {
  source: KnowledgeSource;
  known: OrgConnector[] | undefined;
  unreadable: boolean;
}) {
  const name = source.connector;
  if (!name && source.config?.github_app === true) {
    return (
      <span className="text-xs text-ink-muted" data-testid={`source-connector-${source.id}`}>
        through the platform&apos;s GitHub App: private repositories too
      </span>
    );
  }
  if (!name) {
    const what = source.kind === "web" ? "the public web" : "a public repository";
    return (
      <span className="text-xs text-ink-muted" data-testid={`source-connector-${source.id}`}>
        no connector: {what}
      </span>
    );
  }
  const connector = known?.find((c) => c.name === name);
  return (
    <span className="text-xs" data-testid={`source-connector-${source.id}`}>
      via connector{" "}
      {unreadable ? (
        <strong data-verbatim title="Only the organisation's members with access to its connectors can open it">
          {name}
        </strong>
      ) : (
        <Link href={orgConnectorHref(name)} className="font-medium underline" data-verbatim>
          {name}
        </Link>
      )}
      {connector && <span className="text-ink-muted"> ({connector.type})</span>}
      {known && !connector && <span className="text-danger"> (no longer declared)</span>}
    </span>
  );
}

/**
 * Where a knowledge base's documents are synced from (S33-04): SharePoint or OneDrive through the
 * organisation's `documents` connector (Graph, `Sites.Selected`), a Confluence Cloud space or page tree (S39-20, same kind of connector), an https site, a git repository.
 * The sources come first, each a row with its state in colour (up to date, syncing, failed with its
 * error, never synced) and its gestures; adding one is behind a button, and the form opens on a kind
 * the organisation can actually sync — never on a connected kind it has no connector for.
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
  const [adding, setAdding] = useState(false);
  // The person's choice of kind; until then, the first kind the organisation can sync.
  const [picked, setPicked] = useState<Kind | null>(null);
  // The procedure opens by itself once the person has chosen a kind, not when the page loads.
  const [chosen, setChosen] = useState(false);
  const [fields, setFields] = useState(EMPTY_FIELDS);
  const [refresh, setRefresh] = useState<RefreshDraft>(draftOf());
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState<RefreshDraft>(draftOf());
  const [applyAll, setApplyAll] = useState(false);
  const [attachments, setAttachments] = useState(false);
  const [pageOnly, setPageOnly] = useState(false);
  const [documents, setDocuments] = useState(false);
  const [subPages, setSubPages] = useState(true);
  const [githubApp, setGithubApp] = useState(false);
  const [editApp, setEditApp] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const guard = useEgressGuard();
  // A connected source picks one of the organisation's `documents/<kind>` connectors: never typed.
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.me(), retry: false });
  // Only an organisation administrator may set the flag; the API stays the judge (403/409 are shown as they come).
  const admin = isOrgAdmin(me.data ?? null, org);
  const connectors = useQuery({ queryKey: ["org-connectors", org], queryFn: () => api.orgConnectors(org) });
  const kind: Kind = picked ?? firstUsableKind(connectors.data);
  const pickable = (connectors.data ?? []).filter((c) => c.kind === "documents" && c.type === kind);
  const usable = (k: Kind) => !CONNECTED.includes(k) || !connectors.data || connectors.data.some((c) => c.kind === "documents" && c.type === k);
  const set = (key: keyof typeof fields) => (e: { target: { value: string } }) => setFields({ ...fields, [key]: e.target.value });
  /** Runs a gesture; true when it went through. */
  async function act(gesture: () => Promise<unknown>): Promise<boolean> {
    setError(null);
    let done = true;
    try {
      await gesture();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "refused");
      done = false;
    }
    onChange();
    return done;
  }
  function close() {
    setAdding(false);
    setPicked(null);
    setChosen(false);
    setFields(EMPTY_FIELDS);
    setRefresh(draftOf());
    setAttachments(false);
    setPageOnly(false);
    setDocuments(false);
    setSubPages(true);
    setGithubApp(false);
  }
  return (
    <Card
      title="Sources"
      action={
        canEdit ? (
          <div className="flex flex-wrap justify-end gap-2">
            {sources.length > 0 && (
              <button type="button" className={buttonClasses("secondary", "sm")} onClick={() => void act(() => api.syncKnowledgeBase(org, slug))}>
                Sync all now
              </button>
            )}
            {!adding && (
              <button type="button" className={buttonClasses("primary", "sm")} onClick={() => setAdding(true)}>
                <Plus aria-hidden className="size-3.5" strokeWidth={2} />
                Add a source
              </button>
            )}
          </div>
        ) : undefined
      }
    >
      <div className="space-y-3 text-sm">
        {sources.length === 0 ? (
          <p className="text-ink-muted">No source: the documents come from uploads only.</p>
        ) : (
          <ul className="divide-y divide-line rounded-md border border-line" data-testid="knowledge-sources">
            {sources.map((source) => {
              const state = sourceState(source);
              const Icon = KIND_ICON[source.kind as Kind] ?? Globe;
              return (
                <li key={source.id} className="space-y-2 px-3 py-3" data-state={state.label}>
                  <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
                    <Icon aria-hidden className="mt-0.5 size-4 shrink-0 text-ink-muted" strokeWidth={1.75} />
                    <div className="min-w-0 flex-1 space-y-1">
                      <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <strong>{source.name}</strong>
                        <Badge tone={state.tone} data-testid={`source-state-${source.id}`}>
                          {state.label}
                        </Badge>
                      </p>
                      <p className="flex flex-wrap items-baseline gap-x-2 text-xs text-ink-muted">
                        <span>{KIND[source.kind as Kind] ?? source.kind}</span>
                        <span aria-hidden>·</span>
                        <SourceConnector source={source} known={connectors.data} unreadable={connectors.isError} />
                      </p>
                      <p className="text-xs">
                        {source.last_synced_at ? `Synced ${relative(source.last_synced_at)}` : "Not synced yet"} ·{" "}
                        {syncSummary(source.last_summary)} · {source.document_count ?? 0} document(s) · {refreshLabel(source)} · {nextSyncLabel(source)}
                      </p>
                    </div>
                    {canEdit && (
                      <div className="flex flex-wrap gap-2">
                        <button type="button" className={buttonClasses("secondary", "sm")} onClick={() => void act(() => api.syncKnowledgeSource(org, slug, source.id))}>
                          Sync now
                        </button>
                        <button
                          type="button"
                          className={buttonClasses("ghost", "sm")}
                          aria-expanded={editing === source.id}
                          onClick={() => {
                            setDraft(draftOf(source));
                            setApplyAll(false);
                            setEditApp(source.config?.github_app === true);
                            setEditing(editing === source.id ? null : source.id);
                          }}
                        >
                          Edit refresh
                        </button>
                        <button
                          type="button"
                          className={buttonClasses("danger", "sm")}
                          aria-label={`Remove ${source.name}`}
                          onClick={() => void act(() => api.deleteKnowledgeSource(org, slug, source.id))}
                        >
                          Remove
                        </button>
                      </div>
                    )}
                  </div>
                  {source.last_error && (
                    <p className="rounded border border-failed-line bg-failed-soft px-2 py-1 text-xs text-failed-ink" data-testid={`source-error-${source.id}`}>
                      {source.last_error}
                    </p>
                  )}
                  {truncationsOf(source.last_summary).length > 0 && (
                    <ul className="text-xs text-waiting-ink" data-testid={`source-truncations-${source.id}`}>
                      {truncationsOf(source.last_summary).map((reason) => (
                        <li key={reason}>Cut: {reason}</li>
                      ))}
                    </ul>
                  )}
                  {editing === source.id && (
                    <form
                      className="grid w-full gap-2 rounded-md border border-line p-3 sm:grid-cols-3"
                      aria-label="Edit refresh"
                      onSubmit={(event) => {
                        event.preventDefault();
                        const targets = applyAll ? sources : [source];
                        const offered = githubAppOffered(source.kind, String(source.config?.url ?? ""), admin, source.connector);
                        const appChanged = offered && editApp !== (source.config?.github_app === true);
                        void act(async () => {
                          for (const target of targets) {
                            // The flag is the edited source's own: never copied to the others.
                            const config = appChanged && target.id === source.id ? { config: { ...source.config, github_app: editApp } } : {};
                            await api.updateKnowledgeSource(org, slug, target.id, { ...refreshBody(draft), ...config });
                          }
                          setEditing(null);
                        });
                      }}
                    >
                      <RefreshFields value={draft} onChange={setDraft} />
                      {githubAppOffered(source.kind, String(source.config?.url ?? ""), admin, source.connector) && (
                        <GithubAppField checked={editApp} onChange={setEditApp} />
                      )}
                      <label className="flex items-center gap-1 text-xs">
                        <input type="checkbox" checked={applyAll} onChange={(e) => setApplyAll(e.target.checked)} />
                        apply to all sources
                      </label>
                      <div className="flex gap-2">
                        <button type="submit" className={buttonClasses("primary", "sm")}>
                          Save refresh
                        </button>
                        <button type="button" className={buttonClasses("ghost", "sm")} onClick={() => setEditing(null)}>
                          Cancel
                        </button>
                      </div>
                    </form>
                  )}
                </li>
              );
            })}
          </ul>
        )}
        {canEdit && adding && (
        <form
          aria-label="Add a source"
          className="grid gap-2 rounded-md border border-line p-3 sm:grid-cols-3"
          onSubmit={(event) => {
            event.preventDefault();
            void act(() =>
              guard(
                (allow) =>
                  api.createKnowledgeSource(org, slug, {
                    kind,
                    name: fields.name || KIND[kind],
                    connector: CONNECTED.includes(kind) ? fields.connector : OPTIONALLY_CONNECTED.includes(kind) ? fields.connector.trim() || null : null,
                    config: sourceConfig(kind, { ...fields, attachments, pageOnly, documents, descendants: subPages, githubApp: githubAppOffered(kind, fields.url, admin, fields.connector) && githubApp }),
                    ...refreshBody(refresh),
                    ...egressFlag(allow),
                  }),
                {
                  gesture: `Add the ${KIND[kind]} source ${fields.name || KIND[kind]}`,
                  origin: `knowledge-source:${fields.name || KIND[kind]}`,
                  reason: `the ${KIND[kind]} knowledge source ${fields.name || KIND[kind]}`,
                },
              ),
            ).then((done) => {
              if (done) close();
            });
          }}
        >
          <p className="text-sm font-medium sm:col-span-3">Add a source</p>
          <Labelled label="Kind">
            <Select
              aria-label="source kind"
              value={kind}
              onChange={(e) => {
                setPicked(e.target.value as Kind);
                // A connector is of one kind: the one picked for another kind does not carry over.
                setFields({ ...fields, connector: "" });
                setChosen(true);
              }}
            >
              {(Object.keys(KIND_OPTION) as Kind[]).map((k) => (
                <option key={k} value={k}>
                  {KIND_OPTION[k]}
                  {usable(k) ? "" : " — no connector yet"}
                </option>
              ))}
            </Select>
          </Labelled>
          <div className="flex flex-wrap items-end gap-2 sm:col-span-2" data-testid="source-procedures">
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
                <Labelled label="Connector">
                  <Select aria-label="documents connector" value={fields.connector} onChange={set("connector")}>
                    <option value="">documents connector…</option>
                    {pickable.map((c) => (
                      <option key={c.name} value={c.name}>
                        {c.name}
                      </option>
                    ))}
                  </Select>
                </Labelled>
              ) : (
                <p
                  className="rounded border border-waiting-line bg-waiting-soft px-2 py-1 text-xs text-waiting-ink sm:col-span-2"
                  data-testid="no-documents-connector"
                >
                  This organisation has no {kind === "confluence" ? "Confluence" : "SharePoint"} connector yet: this kind cannot sync
                  until an administrator{" "}
                  <Link className="underline" href={`/admin/connectors?type=documents/${kind}`}>
                    declares a {kind === "confluence" ? "Confluence" : "SharePoint"} connector
                  </Link>
                  .
                </p>
              )}
            </>
          )}
          {kind === "sharepoint" ? (
            <>
              <Input aria-label="site or drive id" placeholder="site id, or drive id (b!…)" value={fields.site} onChange={set("site")} />
              <Input aria-label="folder" placeholder="folder (optional)" value={fields.folder} onChange={set("folder")} />
              <Input aria-label="excluded paths" placeholder="excluded paths: /Archive/*, /*DO NOT USE*" value={fields.exclude} onChange={set("exclude")} />
              <p className="text-xs text-ink-muted sm:col-span-3">
                SharePoint reads the sites granted to its application (<code>Sites.Selected</code>), or what its connector&apos;s service account may read.
              </p>
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
              <p className="text-xs text-ink-muted sm:col-span-3">
                A Confluence source mirrors everything the connector&apos;s account can read in the space: it becomes readable by
                everyone this base is open to. The page id is the number in the page&apos;s URL (<code>/pages/&lt;id&gt;/…</code>).
              </p>
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
              {githubAppOffered(kind, fields.url, admin, fields.connector) && <GithubAppField checked={githubApp} onChange={setGithubApp} />}
            </>
          )}
          <RefreshFields value={refresh} onChange={setRefresh} />
          <div className="flex flex-wrap gap-2 sm:col-span-3">
            <button
              type="submit"
              className={buttonClasses("primary", "sm")}
              disabled={!usable(kind)}
              title={usable(kind) ? undefined : "Declare a connector of this kind first"}
            >
              Add source
            </button>
            <button type="button" className={buttonClasses("ghost", "sm")} onClick={close}>
              Cancel
            </button>
          </div>
        </form>
        )}
        {error && <ErrorNote>{error}</ErrorNote>}
      </div>
    </Card>
  );
}

/** The GitHub App checkbox of a git source, with what it does. */
function GithubAppField({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className="space-y-1 sm:col-span-3" data-testid="github-app-field">
      <label className="flex items-center gap-2 text-xs">
        <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
        {GITHUB_APP_LABEL}
      </label>
      <p className="text-xs text-ink-muted">{GITHUB_APP_HELP}</p>
    </div>
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
