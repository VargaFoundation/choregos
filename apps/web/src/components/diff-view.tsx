// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Badge, Empty, ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { type DiffLine, type ParsedDiffFile, parseUnifiedDiff } from "@/lib/unified-diff";
import type { Artifact } from "@/lib/types";

/** Open every file of a small diff; a large one is read file by file. */
const OPEN_UP_TO = 8;
/** Beyond this the browser, not the reader, is the limit: the download has everything. */
const MAX_LINES_PER_FILE = 1500;

type Stat = NonNullable<NonNullable<Artifact["display"]>["diff"]>;

const LINE_STYLE: Record<DiffLine["kind"], string> = {
  add: "bg-succeeded-soft",
  del: "bg-failed-soft",
  ctx: "",
};
const MARK: Record<DiffLine["kind"], string> = { add: "+", del: "−", ctx: " " };

const NOTE: Record<NonNullable<ParsedDiffFile["note"]>, string> = {
  "no-patch": "The SCM gives no text for this file: empty, binary, or too large.",
  cut: "Cut: the diff of this branch is larger than the platform keeps.",
  withheld: "Withheld: a secret was detected in this file.",
};

/** The hunks a file draws, within the bound, and whether any line was left out. */
function bounded(file: ParsedDiffFile): { hunks: ParsedDiffFile["hunks"]; cut: boolean } {
  const total = file.hunks.reduce((n, h) => n + h.lines.length, 0);
  const hunks = file.hunks.reduce<ParsedDiffFile["hunks"]>((kept, hunk) => {
    const left = MAX_LINES_PER_FILE - kept.reduce((n, h) => n + h.lines.length, 0);
    return left > 0 ? [...kept, { ...hunk, lines: hunk.lines.slice(0, left) }] : kept;
  }, []);
  return { hunks, cut: total > MAX_LINES_PER_FILE };
}

function Lines({ file }: { file: ParsedDiffFile }) {
  const { hunks, cut } = bounded(file);
  return (
    <div className="overflow-x-auto font-mono text-xs" data-testid="diff-lines">
      {hunks.map((hunk, index) => (
        <div key={index}>
          <div className="bg-surface-muted px-2 py-0.5 text-ink-muted" data-verbatim>
            {hunk.header}
          </div>
          {hunk.lines.map((line, at) => (
            <div key={at} className={cn("flex min-w-max", LINE_STYLE[line.kind])} data-kind={line.kind}>
              <span aria-hidden className="w-10 shrink-0 select-none pr-1 text-right text-ink-muted">
                {line.oldNumber ?? ""}
              </span>
              <span aria-hidden className="w-10 shrink-0 select-none pr-1 text-right text-ink-muted">
                {line.newNumber ?? ""}
              </span>
              <span aria-hidden className="w-4 shrink-0 select-none text-center">
                {MARK[line.kind]}
              </span>
              <span className="whitespace-pre pr-2" data-verbatim>
                {line.text}
              </span>
            </div>
          ))}
        </div>
      ))}
      {cut ? (
        <p className="px-2 py-1 font-sans text-ink-muted" data-testid="diff-file-cut">
          Only the first {MAX_LINES_PER_FILE} lines are drawn: download the diff to read the rest.
        </p>
      ) : null}
    </div>
  );
}

const STATUS_WORD: Partial<Record<ParsedDiffFile["status"], string>> = {
  added: "Added",
  removed: "Removed",
  renamed: "Renamed",
};

function FileBlock({ file, open, outOfScope }: { file: ParsedDiffFile; open: boolean; outOfScope: boolean }) {
  return (
    <details open={open} className="border border-line" data-testid="diff-file" data-path={file.path}>
      <summary className="flex cursor-pointer flex-wrap items-center gap-x-3 gap-y-1 px-2 py-1.5 text-xs">
        <span className="min-w-0 break-all font-mono text-ink" data-verbatim>
          {file.status === "renamed" ? `${file.oldPath} → ${file.path}` : file.path}
        </span>
        {STATUS_WORD[file.status] && (
          <Badge tone="neutral" dot={false}>
            {STATUS_WORD[file.status]}
          </Badge>
        )}
        <span className="text-succeeded-ink">+{file.additions}</span>
        <span className="text-failed-ink">−{file.deletions}</span>
        {outOfScope && (
          <Badge tone="danger" data-testid="diff-out-of-scope">
            Out of scope
          </Badge>
        )}
      </summary>
      {file.note ? <p className="border-t border-line px-2 py-1.5 text-xs text-ink-muted">{NOTE[file.note]}</p> : null}
      {file.hunks.length > 0 ? (
        <div className="border-t border-line">
          <Lines file={file} />
        </div>
      ) : null}
    </details>
  );
}

/** A branch's diff, file by file, measured from the SCM (ADR 0129 §7). */
export function DiffView({
  text,
  stat,
  isOutOfScope = () => false,
}: {
  text: string;
  stat?: Stat | null;
  isOutOfScope?: (path: string) => boolean;
}) {
  const files = useMemo(() => parseUnifiedDiff(text), [text]);
  if (files.length === 0) return <Empty>This diff is empty.</Empty>;
  const additions = stat?.additions ?? files.reduce((n, f) => n + f.additions, 0);
  const deletions = stat?.deletions ?? files.reduce((n, f) => n + f.deletions, 0);
  return (
    <div className="space-y-2" data-testid="diff-view">
      <p className="flex flex-wrap items-center gap-x-3 text-xs text-ink-muted" data-testid="diff-summary">
        <span>
          {files.length} {files.length === 1 ? "file" : "files"}
        </span>
        <span className="text-succeeded-ink">+{additions}</span>
        <span className="text-failed-ink">−{deletions}</span>
      </p>
      {stat?.withheld?.length ? (
        <ErrorNote>
          <span data-testid="diff-withheld">
            The patch of a file is withheld when it holds what looks like a secret ({stat.withheld.join(", ")}): only its name and counters are kept.
          </span>
        </ErrorNote>
      ) : null}
      {stat?.truncated ? (
        <p className="text-xs text-ink-muted" data-testid="diff-truncated">
          This diff is larger than the platform keeps: the files that did not fit are listed without their patch.
        </p>
      ) : null}
      {files.map((file, index) => (
        <FileBlock key={`${index}:${file.path}`} file={file} open={files.length <= OPEN_UP_TO} outOfScope={isOutOfScope(file.path)} />
      ))}
    </div>
  );
}

/** A `diff` revision, read from the API, whose digest it checked before serving it. */
export function StoredDiff({
  itemId,
  artifact,
  isOutOfScope,
}: {
  itemId: string;
  artifact: Artifact;
  isOutOfScope?: (path: string) => boolean;
}) {
  const ref = `${artifact.name}@${artifact.version}`;
  const content = useQuery({
    queryKey: ["artifact-content", itemId, ref],
    queryFn: () => api.artifactContent(itemId, ref),
    retry: false,
  });
  const [text, setText] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    void content.data?.text().then((value) => alive && setText(value));
    return () => {
      alive = false;
    };
  }, [content.data]);
  if (content.error) return <ErrorNote>This diff is not shown: {(content.error as Error).message}</ErrorNote>;
  if (text === null) return <Empty>Loading the diff…</Empty>;
  return <DiffView text={text} stat={artifact.display?.diff} isOutOfScope={isOutOfScope} />;
}
