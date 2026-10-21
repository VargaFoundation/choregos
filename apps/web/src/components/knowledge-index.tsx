// SPDX-License-Identifier: Apache-2.0
"use client";

import { useState } from "react";
import { Card, ErrorNote, Input, Textarea, buttonClasses } from "@/components/ui";
import { api } from "@/lib/api";
import { formatEvalSet, parseEvalSet, recallLabel, searchModeLabel } from "@/lib/knowledge";
import type { KnowledgeBase, KnowledgeSearchResult } from "@/lib/types";

/**
 * An `index` knowledge base (ADR 0051): how it ranks here, a search that cites, and its eval set —
 * questions and the documents expected to answer them — with the recall@5 it last measured.
 */
export function KnowledgeIndex({
  org,
  base,
  onChange,
  canEdit = true,
}: {
  org: string;
  base: KnowledgeBase;
  onChange: () => void;
  /** `can.edit` of the base (S42-11): search stays open to readers, reindexing and the eval set do not. */
  canEdit?: boolean;
}) {
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<KnowledgeSearchResult | null>(null);
  const [evalText, setEvalText] = useState(() => formatEvalSet(base.eval_set));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function act(gesture: () => Promise<unknown>) {
    setError(null);
    setBusy(true);
    try {
      await gesture();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "refused");
    } finally {
      setBusy(false);
    }
    onChange();
  }
  const index = base.index;
  const evaluation = base.eval_result;
  return (
    <>
      <Card
        title="Index"
        action={
          canEdit ? (
            <button type="button" className={buttonClasses("ghost", "sm")} disabled={busy} onClick={() => void act(() => api.reindexKnowledgeBase(org, base.slug))}>
              Reindex
            </button>
          ) : undefined
        }
      >
        <div className="space-y-3 text-sm">
          <p data-testid="knowledge-index-state">
            {index ? `${index.documents ?? 0} document(s) in ${index.chunks ?? 0} chunks · ${searchModeLabel(index.mode)}` : "Not indexed"}
          </p>
          <form
            className="flex flex-wrap gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              if (query.trim()) void act(async () => setResult(await api.searchKnowledgeBase(org, base.slug, query)));
            }}
          >
            <Input
              aria-label="Search the knowledge base"
              className="min-w-0 flex-1"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="which TLS version is required in transit?"
            />
            <button type="submit" className={buttonClasses("secondary", "sm")} disabled={busy}>
              Search
            </button>
          </form>
          {result && (
            <ol className="space-y-2" data-testid="knowledge-hits">
              {result.hits.length === 0 && <li className="text-ink-muted">Nothing matches</li>}
              {result.hits.map((hit, n) => (
                <li key={`${hit.document_id}-${n}`} className="border-l-2 border-line pl-2">
                  <p>
                    <span className="font-mono text-xs text-ink-muted">[{n + 1}]</span> <strong>{hit.document}</strong>{" "}
                    <span className="text-xs text-ink-muted">
                      {hit.filename} — {hit.heading_path.join(" > ") || "top of the document"}
                    </span>
                  </p>
                  <p className="whitespace-pre-wrap text-xs">{hit.excerpt}</p>
                </li>
              ))}
            </ol>
          )}
        </div>
      </Card>
      <Card title="Eval set">
        <div className="space-y-2 text-sm">
          <p data-testid="knowledge-recall">
            recall@{evaluation?.k ?? 5}: <strong>{recallLabel(evaluation?.recall)}</strong>
            {evaluation?.ran_at && <span className="text-xs text-ink-muted"> · {evaluation.mode} · {evaluation.ran_at.slice(0, 16).replace("T", " ")}</span>}
          </p>
          {evaluation?.cases && (
            <ul className="text-xs">
              {evaluation.cases.map((c, n) => (
                <li key={n}>
                  {(c.found ?? []).length === (c.expected ?? []).length ? "✓" : "✗"} {c.question}{" "}
                  <span className="text-ink-muted">— expected {(c.expected ?? []).join(", ")}; got {(c.returned ?? []).join(", ") || "nothing"}</span>
                </li>
              ))}
            </ul>
          )}
          <label className="block space-y-1">
            <span className="text-xs text-ink-muted">One question per line: question | expected document, another</span>
            <Textarea aria-label="Eval set" rows={5} value={evalText} readOnly={!canEdit} onChange={(e) => setEvalText(e.target.value)} />
          </label>
          {canEdit && <div className="flex gap-2">
            <button
              type="button"
              className={buttonClasses("secondary", "sm")}
              disabled={busy}
              onClick={() => void act(() => api.putKnowledgeEvalSet(org, base.slug, parseEvalSet(evalText)))}
            >
              Save
            </button>
            <button type="button" className={buttonClasses("primary", "sm")} disabled={busy} onClick={() => void act(() => api.runKnowledgeEval(org, base.slug))}>
              Measure recall
            </button>
          </div>}
          {error && <ErrorNote>{error}</ErrorNote>}
        </div>
      </Card>
    </>
  );
}
