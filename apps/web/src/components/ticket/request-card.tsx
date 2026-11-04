// SPDX-License-Identifier: Apache-2.0
"use client";

import { FileText } from "lucide-react";
import { MarkdownView } from "@/components/markdown-view";
import { Card } from "@/components/ui";
import { libelleDeCode, toSentenceCase } from "@/lib/format";

/**
 * The request itself (S55-05): what was asked when the ticket was filed — its text and its fields. The
 * ticket's page never showed it: one had to open the tracker to know what the ticket was about.
 */
export function RequestCard({ body, fields }: { body?: string | null; fields?: Record<string, unknown> | null }) {
  const entries = Object.entries(fields ?? {}).filter(([, value]) => value !== null && value !== undefined && value !== "");
  const text = body?.trim();
  return (
    <Card
      title={
        <span className="inline-flex items-center gap-2">
          <FileText aria-hidden className="size-4 text-ink-muted" /> The request
        </span>
      }
      data-testid="request-card"
    >
      {!text && entries.length === 0 ? (
        <p className="text-sm text-ink-muted">The request was filed with a title only.</p>
      ) : (
        <div className="space-y-4">
          {entries.length > 0 && (
            <dl className="grid grid-cols-[minmax(8rem,auto)_1fr] gap-x-4 gap-y-1.5 text-sm" aria-label="Fields of the request">
              {entries.map(([name, value]) => (
                <div key={name} className="contents">
                  <dt className="text-ink-muted">{toSentenceCase(libelleDeCode(name))}</dt>
                  <dd data-verbatim className="min-w-0 break-words">
                    {fieldValue(value)}
                  </dd>
                </div>
              ))}
            </dl>
          )}
          {text && <LongText text={text} />}
        </div>
      )}
    </Card>
  );
}

function fieldValue(value: unknown): string {
  if (Array.isArray(value)) return value.map(fieldValue).join(", ");
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "object" && value !== null) return JSON.stringify(value);
  return String(value);
}

/** Past this many characters a text is folded: its beginning, and a button for the rest. */
export const FOLD_AFTER = 700;

/**
 * Markdown that may be long — a request, an agent's summary —: rendered as Markdown, never as one block
 * of raw text, and folded past a screenful so that what comes after it stays in sight.
 */
export function LongText({ text }: { text: string }) {
  if (text.length <= FOLD_AFTER) return <MarkdownView text={text} />;
  return (
    <details className="group">
      <summary className="cursor-pointer list-none [&::-webkit-details-marker]:hidden">
        <div className="relative max-h-48 overflow-hidden group-open:hidden">
          <MarkdownView text={text.slice(0, FOLD_AFTER)} />
          <div aria-hidden className="absolute inset-x-0 bottom-0 h-12 bg-gradient-to-t from-surface to-transparent" />
        </div>
        <span className="mt-2 inline-block text-sm text-accent-strong underline group-open:hidden">Show all</span>
        <span className="hidden text-sm text-accent-strong underline group-open:inline">Show less</span>
      </summary>
      <div className="mt-2">
        <MarkdownView text={text} />
      </div>
    </details>
  );
}
