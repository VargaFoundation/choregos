// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useRef, useState } from "react";
import { KnowledgeAccess } from "@/components/knowledge-access";
import { KnowledgeDistill } from "@/components/knowledge-distill";
import { KnowledgeServing, KnowledgeStatusBanner, KnowledgeUsedBy } from "@/components/knowledge-overview";
import { KnowledgeSources } from "@/components/knowledge-sources";
import { Badge, Card, Dot, Empty, ErrorNote, Heading, buttonClasses, type Tone } from "@/components/ui";
import { FileText, Upload } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { relative } from "@/lib/format";
import { DOCUMENT_STATUS, distillationLabel, permissionsOf, stillConverting } from "@/lib/knowledge";
import { useSession } from "@/lib/session";
import { skillHref } from "@/lib/skill-slug";

// Only an `index` base shows the search and eval panel: loaded when needed, outside the page's chunk.
const KnowledgeIndex = dynamic(() => import("@/components/knowledge-index").then((m) => m.KnowledgeIndex), {
  ssr: false,
  loading: () => <p className="text-sm text-ink-muted">Loading the index…</p>,
});

type Filter = "all" | "ready" | "working" | "failed";
/** The documents list's filters: pending and converting are one group, work still running. */
const FILTERS: { key: Filter; label: string; tone?: Tone }[] = [
  { key: "all", label: "All" },
  { key: "ready", label: "Ready", tone: "ok" },
  { key: "working", label: "Converting", tone: "running" },
  { key: "failed", label: "Failed", tone: "danger" },
];
const statusGroup = (status: string): Filter => (status === "pending" || status === "converting" ? "working" : (status as Filter));
const countOf = (counts: Record<string, number>, key: Filter): number =>
  key === "working" ? (counts.pending ?? 0) + (counts.converting ?? 0) : (counts[key] ?? 0);

/**
 * One knowledge base: first whether it can be used now (a status banner), which agents read it and
 * who reads it, how runs are served it; then its documents with where each conversion stands
 * (upload, retry, read), its sources, its index, and who holds a role on it. Shown
 * by the organisation's registry (`/knowledge/<kb>`) and inside a project (`/p/<p>/knowledge/<kb>`,
 * S39-14): `back` says where the crumb leads, `headingLevel` keeps one h1 per page (a project's
 * layout already prints the project name as the h1).
 */
export function KnowledgeBaseView({
  slug,
  back,
  headingLevel = "h1",
}: {
  slug: string;
  back: { href: string; label: string };
  headingLevel?: "h1" | "h2";
}) {
  const { org } = useSession();
  const client = useQueryClient();
  const base = useQuery({
    queryKey: ["knowledge-base", org, slug],
    queryFn: () => api.knowledgeBase(org, slug),
    // A conversion runs in the background: the page follows it until nothing is pending.
    refetchInterval: (query) =>
      stillConverting(query.state.data) || query.state.data?.distillation?.status === "running" ? 2000 : false,
  });
  const [shown, setShown] = useState<string | null>(null);
  const document = useQuery({
    queryKey: ["knowledge-document", org, slug, shown],
    queryFn: () => api.knowledgeDocument(org, slug, shown ?? ""),
    enabled: shown !== null,
  });
  const file = useRef<HTMLInputElement>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const refresh = () => client.invalidateQueries({ queryKey: ["knowledge-base", org, slug] });
  async function upload(files: FileList) {
    setError(null);
    setNotice(null);
    for (const one of Array.from(files)) {
      try {
        const sent = await api.uploadKnowledgeDocument(org, slug, one);
        setNotice(`${sent.filename}: ${sent.status}`);
      } catch (cause) {
        setError(`${one.name}: ${cause instanceof Error ? cause.message : "Refused"}`);
      }
    }
    if (file.current) file.current.value = "";
    await refresh();
  }
  async function act(gesture: () => Promise<unknown>) {
    setError(null);
    try {
      await gesture();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Refused");
    }
    await refresh();
  }
  if (base.error) return <ErrorNote>{String(base.error)}</ErrorNote>;
  if (!base.data) return <Empty>Reading the knowledge base…</Empty>;
  const documents = base.data.documents ?? [];
  const can = permissionsOf(base.data);
  // The server's sentence for what this person may not do: shown where the gesture would be.
  const why = base.data.cannot_reason ?? undefined;
  const counts: Record<string, number> = {};
  for (const doc of documents) counts[doc.status] = (counts[doc.status] ?? 0) + 1;
  // A filter whose last document left (converted, deleted) falls back to all of them.
  const active: Filter = filter !== "all" && countOf(counts, filter) === 0 ? "all" : filter;
  const listed = active === "all" ? documents : documents.filter((doc) => statusGroup(doc.status) === active);
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <div className="flex flex-wrap items-baseline gap-3">
          <Link href={back.href} className="text-sm text-ink-muted hover:underline">
            {back.label}
          </Link>
          <span aria-hidden className="text-ink-muted">
            /
          </span>
          <Heading as={headingLevel} size="lg">
            {base.data.name}
          </Heading>
          <span className="font-mono text-xs text-ink-muted">{base.data.slug}</span>
        </div>
        {base.data.description && <p className="max-w-3xl text-sm text-ink-muted">{base.data.description}</p>}
      </div>
      <KnowledgeStatusBanner base={base.data} />
      <div className="grid gap-6 lg:grid-cols-2">
        <KnowledgeUsedBy base={base.data} />
        <KnowledgeServing
          base={base.data}
          canEdit={can.edit}
          why={why}
          onChange={(mode) => void act(() => api.updateKnowledgeBase(org, slug, { serving_mode: mode }))}
        >
          <div className="space-y-1 border-t border-line pt-3" data-testid="knowledge-distillation">
            <p className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-medium text-ink-muted">Principles skill</span>
              <span>{distillationLabel(base.data.distillation)}</span>
              {base.data.distillation?.skill ? (
                <Link href={skillHref(String(base.data.distillation.skill))} className="underline">
                  Review
                </Link>
              ) : null}
            </p>
            <p className="text-xs text-ink-muted">
              A skill of short principles distilled from the documents, each citing its source, for agents to load.
            </p>
            {/* The run and the estimate are cards: each takes a line of its own under the buttons. */}
            <div className="flex flex-wrap items-center gap-2 [&>div]:basis-full [&>section]:basis-full">
              {can.distill ? (
                <KnowledgeDistill org={org} slug={slug} onStarted={refresh} distillation={base.data.distillation} />
              ) : (
                <>
                  <button type="button" className={buttonClasses("secondary", "sm")} disabled title={why}>
                    Distill
                  </button>
                  {why && <span className="text-xs text-ink-muted">{why}</span>}
                </>
              )}
            </div>
          </div>
        </KnowledgeServing>
      </div>
      <Card
        title="Documents"
        icon={FileText}
        action={
          can.edit ? (
            <label className={cn(buttonClasses("primary", "sm"), "cursor-pointer focus-within:outline-2 focus-within:outline-accent")}>
              <Upload aria-hidden className="size-3.5" strokeWidth={2} />
              Upload documents
              <input
                ref={file}
                type="file"
                multiple
                aria-label="Documents to upload"
                className="sr-only"
                accept=".docx,.pptx,.xlsx,.pdf,.md,.markdown,.html,.htm,.txt,.yaml,.yml,.json,.toml"
                onChange={(event) => {
                  if (event.target.files?.length) void upload(event.target.files);
                }}
              />
            </label>
          ) : undefined
        }
      >
        <div className="space-y-3">
          {can.edit ? (
            <p className="text-xs text-ink-muted">
              Office, PDF, HTML, markdown or text, converted in the background; a scanned PDF fails (no OCR).
            </p>
          ) : (
            <p className="text-sm text-ink-muted" data-testid="knowledge-read-only">
              {why ?? "You can read this knowledge base but not change it."}
            </p>
          )}
          {notice && <p role="status" className="text-sm">{notice}</p>}
          {error && <ErrorNote>{error}</ErrorNote>}
          {documents.length === 0 ? (
            <Empty title="No document yet">
              {can.edit ? "Upload files above, or add a source below." : "Nobody has added a document yet."}
            </Empty>
          ) : (
            <>
              <div className="flex flex-wrap gap-2" role="group" aria-label="Filter documents by status" data-testid="knowledge-document-filter">
                {FILTERS.filter((f) => f.key === "all" || countOf(counts, f.key) > 0).map((f) => (
                  <button
                    key={f.key}
                    type="button"
                    aria-pressed={active === f.key}
                    onClick={() => setFilter(f.key)}
                    className={cn(
                      "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium",
                      active === f.key ? "border-line-strong bg-surface-muted text-ink" : "border-line text-ink-muted hover:text-ink",
                    )}
                  >
                    {f.tone && <Dot tone={f.tone} />}
                    {f.label}
                    <span className="font-mono tabular-nums">{f.key === "all" ? documents.length : countOf(counts, f.key)}</span>
                  </button>
                ))}
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm" data-testid="knowledge-documents">
                  <thead className="text-left text-xs text-ink-muted">
                    <tr>
                      <th className="py-1 font-normal">Document</th>
                      <th className="py-1 font-normal">Status</th>
                      <th className="py-1 font-normal">Uploaded</th>
                      <th className="py-1 font-normal" />
                    </tr>
                  </thead>
                  <tbody>
                    {listed.map((doc) => {
                      const status = DOCUMENT_STATUS[doc.status] ?? { label: doc.status, tone: "neutral" as const };
                      return (
                        <tr key={doc.id} className="border-t border-line align-top" data-status={doc.status}>
                          <td className="py-2 pr-3">
                            <button type="button" className="text-left font-medium hover:underline" onClick={() => setShown(doc.id)}>
                              {doc.title}
                            </button>
                            <p className="font-mono text-xs text-ink-muted">
                              {doc.filename} · {Math.max(1, Math.round(doc.size_bytes / 1024))} KiB
                              {doc.source_url && (
                                <>
                                  {" · "}
                                  <a href={doc.source_url} className="underline" target="_blank" rel="noreferrer">
                                    at the source
                                  </a>
                                </>
                              )}
                            </p>
                            {doc.error && <p className="mt-1 text-xs text-failed-ink">{doc.error}</p>}
                            {(doc.warnings ?? []).length > 0 && (
                              <ul className="mt-1 text-xs text-waiting-ink" data-testid={`document-warnings-${doc.id}`}>
                                {(doc.warnings ?? []).map((warning) => (
                                  <li key={warning}>Cut: {warning}</li>
                                ))}
                              </ul>
                            )}
                          </td>
                          <td className="py-2 pr-3">
                            <Badge tone={status.tone}>{status.label}</Badge>
                          </td>
                          <td className="py-2 pr-3 text-xs text-ink-muted">{doc.created_at ? relative(doc.created_at) : "—"}</td>
                          <td className="py-2">
                            <div className="flex justify-end gap-2">
                              {doc.status === "failed" && can.edit && (
                                <button
                                  type="button"
                                  className={buttonClasses("secondary", "sm")}
                                  onClick={() => void act(() => api.convertKnowledgeDocument(org, slug, doc.id))}
                                >
                                  Convert again
                                </button>
                              )}
                              {can.edit ? (
                                <button
                                  type="button"
                                  className={buttonClasses("danger", "sm")}
                                  onClick={() => void act(() => api.deleteKnowledgeDocument(org, slug, doc.id))}
                                >
                                  Delete
                                </button>
                              ) : (
                                <button type="button" className={buttonClasses("danger", "sm")} disabled title={why}>
                                  Delete
                                </button>
                              )}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      </Card>
      <KnowledgeSources org={org} slug={slug} sources={base.data.sources ?? []} onChange={() => void refresh()} canEdit={can.edit} />
      {base.data.serving_mode === "index" && <KnowledgeIndex org={org} base={base.data} onChange={() => void refresh()} canEdit={can.edit} />}
      <KnowledgeAccess org={org} base={base.data} onChange={() => void refresh()} />
      {shown && (
        <Card
          title={document.data?.title ?? "Document"}
          action={
            <button type="button" className={buttonClasses("ghost", "sm")} onClick={() => setShown(null)}>
              Close
            </button>
          }
        >
          {document.error ? (
            <ErrorNote>{String(document.error)}</ErrorNote>
          ) : !document.data ? (
            <p className="text-sm text-ink-muted">reading…</p>
          ) : document.data.markdown ? (
            <>
              {(document.data.warnings ?? []).map((warning) => (
                <p key={warning} className="mb-2 text-xs text-warn" data-testid="knowledge-document-warning">
                  Cut: {warning}
                </p>
              ))}
              <pre className="max-h-[32rem] overflow-auto whitespace-pre-wrap font-mono text-xs" data-testid="knowledge-markdown">
                {document.data.markdown}
              </pre>
            </>
          ) : (
            <p className="text-sm text-ink-muted">not converted ({document.data.status})</p>
          )}
        </Card>
      )}
    </div>
  );
}
