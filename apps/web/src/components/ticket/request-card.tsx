// SPDX-License-Identifier: Apache-2.0
"use client";

import { FileText } from "lucide-react";
import { MarkdownView } from "@/components/markdown-view";
import { Card } from "@/components/ui";
import { libelleDeCode, toSentenceCase } from "@/lib/format";

/**
 * The request itself (S55-05): what was asked when the ticket was filed — its text and its fields. The
 * ticket's page never showed it: one had to open the tracker to know what the ticket was about. A field
 * is labelled with the title its workflow gives it (`titles`, from `metadata.inputs`, S55-14), else with
 * its name, humanised.
 */
export function RequestCard({
  body,
  fields,
  titles = {},
}: {
  body?: string | null;
  fields?: Record<string, unknown> | null;
  titles?: Record<string, string>;
}) {
  const tracker = trackerOf(fields?.tracker);
  const entries = Object.entries(fields ?? {}).filter(
    ([name, value]) => value !== null && value !== undefined && value !== "" && !(name === "tracker" && tracker),
  );
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
      {!text && entries.length === 0 && !tracker ? (
        <p className="text-sm text-ink-muted">The request was filed with a title only.</p>
      ) : (
        <div className="space-y-4">
          {tracker && <TrackerFacts tracker={tracker} />}
          {entries.length > 0 && (
            <dl className="grid grid-cols-[minmax(8rem,auto)_1fr] gap-x-4 gap-y-1.5 text-sm" aria-label="Fields of the request">
              {entries.map(([name, value]) => (
                <div key={name} className="contents">
                  <dt className="text-ink-muted">{toSentenceCase(titles[name] ?? libelleDeCode(name))}</dt>
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
  if (typeof value === "object" && value !== null)
    return Object.entries(value as Record<string, unknown>)
      .filter(([, v]) => v !== null && v !== undefined && v !== "")
      .map(([k, v]) => `${toSentenceCase(libelleDeCode(k))}: ${fieldValue(v)}`)
      .join(" · ");
  return String(value);
}

type TrackerPerson = { display_name?: string | null; email?: string | null; account_id?: string | null };
type Tracker = {
  key?: string;
  kind?: string;
  labels: string[];
  creator: TrackerPerson | null;
  reporter: TrackerPerson | null;
  answers: [string, string][];
};

/** Jira's internal ordering key and other bookkeeping a person never filled in. */
const NOT_AN_ANSWER = new Set(["Rank"]);

/**
 * What the tracker knows about the ticket (`fields.tracker`, S51-04): who filed it, its labels and the
 * answers of its form. It was shown as one line of JSON; it is read as facts.
 */
export function trackerOf(value: unknown): Tracker | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const raw = value as Record<string, unknown>;
  const person = (p: unknown): TrackerPerson | null =>
    typeof p === "object" && p !== null ? (p as TrackerPerson) : null;
  const answers = Object.entries((raw.answers as Record<string, unknown>) ?? {})
    .filter(([name, v]) => !NOT_AN_ANSWER.has(name) && v !== null && v !== undefined && v !== "")
    .map(([name, v]): [string, string] => [name, fieldValue(v)]);
  return {
    key: typeof raw.key === "string" ? raw.key : undefined,
    kind: typeof raw.kind === "string" ? raw.kind : undefined,
    labels: Array.isArray(raw.labels) ? raw.labels.map(String) : [],
    creator: person(raw.creator),
    reporter: person(raw.reporter),
    answers,
  };
}

function personName(p: TrackerPerson | null): string | null {
  if (!p) return null;
  return p.display_name || p.email || null;
}

function TrackerFacts({ tracker }: { tracker: Tracker }) {
  const filedBy = personName(tracker.reporter);
  const createdBy = personName(tracker.creator);
  const tracked = [tracker.kind ? toSentenceCase(tracker.kind) : null, tracker.key].filter(Boolean).join(" ");
  return (
    <dl
      className="grid grid-cols-[minmax(8rem,auto)_1fr] gap-x-4 gap-y-1.5 text-sm"
      aria-label="What the tracker knows"
      data-testid="tracker-facts"
    >
      {tracked && (
        <div className="contents">
          <dt className="text-ink-muted">Tracker</dt>
          <dd>{tracked}</dd>
        </div>
      )}
      {filedBy && (
        <div className="contents">
          <dt className="text-ink-muted">Filed by</dt>
          <dd>
            {filedBy}
            {createdBy && createdBy !== filedBy ? <span className="text-ink-muted"> (created by {createdBy})</span> : null}
          </dd>
        </div>
      )}
      {tracker.labels.length > 0 && (
        <div className="contents">
          <dt className="text-ink-muted">Labels</dt>
          <dd className="flex flex-wrap gap-1">
            {tracker.labels.map((label) => (
              <span key={label} className="rounded-full border border-line px-2 py-0.5 font-mono text-xs">
                {label}
              </span>
            ))}
          </dd>
        </div>
      )}
      {tracker.answers.map(([name, value]) => (
        <div key={name} className="contents">
          <dt className="text-ink-muted">{name}</dt>
          <dd data-verbatim className="min-w-0 break-words">
            {value}
          </dd>
        </div>
      ))}
    </dl>
  );
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
