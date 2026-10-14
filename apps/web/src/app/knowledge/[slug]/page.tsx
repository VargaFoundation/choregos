// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { use, useRef, useState } from "react";
import { Badge, Card, Empty, ErrorNote, Heading, buttonClasses, type Tone } from "@/components/ui";
import { api } from "@/lib/api";
import { relative } from "@/lib/format";
import { scopeLabel, statusSummary, stillConverting } from "@/lib/knowledge";
import { useSession } from "@/lib/session";

const TONE: Record<string, Tone> = { ready: "ok", failed: "danger", pending: "neutral", converting: "neutral" };

/** One knowledge base: its documents and where each conversion stands; upload, retry, read. */
export default function KnowledgeBasePage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const { org } = useSession();
  const client = useQueryClient();
  const base = useQuery({
    queryKey: ["knowledge-base", org, slug],
    queryFn: () => api.knowledgeBase(org, slug),
    // A conversion runs in the background: the page follows it until nothing is pending.
    refetchInterval: (query) => (stillConverting(query.state.data) ? 2000 : false),
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
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-baseline gap-3">
        <Link href="/knowledge" className="text-sm text-ink-muted hover:underline">
          Knowledge
        </Link>
        <span aria-hidden className="text-ink-muted">
          /
        </span>
        <Heading as="h1" size="lg">
          {base.data.name}
        </Heading>
        <span className="font-mono text-xs text-ink-muted">{base.data.slug}</span>
      </div>
      <p className="text-sm text-ink-muted">
        {scopeLabel(base.data)} · served as {base.data.serving_mode} · {statusSummary(base.data.documents_by_status)}
        {base.data.status !== "active" && ` · ${base.data.status}`}
      </p>
      {base.data.description && <p className="max-w-3xl text-sm">{base.data.description}</p>}
      <Card title="Documents">
        {documents.length === 0 ? (
          <Empty title="No document yet">Upload the corpus below.</Empty>
        ) : (
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
                {documents.map((doc) => (
                  <tr key={doc.id} className="border-t border-line align-top">
                    <td className="py-2">
                      <button type="button" className="text-left font-medium hover:underline" onClick={() => setShown(doc.id)}>
                        {doc.title}
                      </button>
                      <p className="font-mono text-xs text-ink-muted">
                        {doc.filename} · {Math.max(1, Math.round(doc.size_bytes / 1024))} KiB
                      </p>
                      {doc.error && <p className="text-xs text-danger">{doc.error}</p>}
                    </td>
                    <td>
                      <Badge tone={TONE[doc.status] ?? "neutral"}>{doc.status}</Badge>
                    </td>
                    <td className="text-xs text-ink-muted">{doc.created_at ? relative(doc.created_at) : "—"}</td>
                    <td className="space-x-2 text-right text-xs">
                      {doc.status === "failed" && (
                        <button type="button" className="underline" onClick={() => void act(() => api.convertKnowledgeDocument(org, slug, doc.id))}>
                          Convert again
                        </button>
                      )}
                      <button type="button" className="underline" onClick={() => void act(() => api.deleteKnowledgeDocument(org, slug, doc.id))}>
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <Card title="Upload documents">
        <div className="space-y-2 text-sm">
          <p className="text-ink-muted">
            Word, PowerPoint, Excel, PDF, HTML, markdown or text. Each file is converted to markdown in the background: headings,
            slide titles and tables are kept; a scanned PDF needs OCR and fails.
          </p>
          <input
            ref={file}
            type="file"
            multiple
            aria-label="Documents to upload"
            className="min-w-0 max-w-full"
            accept=".docx,.pptx,.xlsx,.pdf,.md,.markdown,.html,.htm,.txt"
            onChange={(event) => {
              if (event.target.files?.length) void upload(event.target.files);
            }}
          />
          {notice && <p role="status">{notice}</p>}
          {error && <ErrorNote>{error}</ErrorNote>}
        </div>
      </Card>
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
            <pre className="max-h-[32rem] overflow-auto whitespace-pre-wrap font-mono text-xs" data-testid="knowledge-markdown">
              {document.data.markdown}
            </pre>
          ) : (
            <p className="text-sm text-ink-muted">not converted ({document.data.status})</p>
          )}
        </Card>
      )}
    </div>
  );
}
