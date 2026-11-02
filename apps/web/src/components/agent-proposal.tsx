// SPDX-License-Identifier: Apache-2.0
"use client";

import Link from "next/link";
import { MarkdownView } from "@/components/markdown-view";
import { Badge } from "@/components/ui";
import type { Run } from "@/lib/types";

/** The output a reviewer reads first; the other `*_markdown` outputs follow it. */
const FIRST = "summary_markdown";

/** The latest agent run that left something to read: a title or a markdown output. */
export function latestProposal(runs: readonly Run[] | undefined): Run | null {
  const readable = (runs ?? []).filter((run) => {
    const outputs = run.result?.outputs ?? {};
    return typeof outputs.title === "string" || markdownOutputs(run).length > 0;
  });
  const when = (run: Run) => Date.parse(run.ended_at ?? run.started_at ?? "") || 0;
  return [...readable].sort((a, b) => when(b) - when(a))[0] ?? null;
}

function markdownOutputs(run: Run): Array<[string, string]> {
  const entries = Object.entries(run.result?.outputs ?? {}).filter(
    (entry): entry is [string, string] => entry[0].endsWith("_markdown") && typeof entry[1] === "string" && entry[1].trim() !== "",
  );
  return entries.sort(([a], [b]) => Number(b === FIRST) - Number(a === FIRST) || a.localeCompare(b));
}

/** Whether the run, or the work item, changed anything in a repository. */
function changedTheRepository(run: Run, prUrl: string | null | undefined): boolean {
  const artifacts = run.result?.artifacts;
  return Boolean(
    prUrl || artifacts?.pr_url || artifacts?.branch || (artifacts?.commits?.length ?? 0) > 0 || (run.result?.evidence?.diff_files ?? 0) > 0,
  );
}

/**
 * What the agent proposes, where the person who decides reads it: the latest agent step's title,
 * outcome, confidence and markdown outputs, rendered. A human task after an agent step (fon-access
 * `t-apply` after `t-analyse`) showed an empty line here, and the proposal was only in the Artifacts
 * card, where a YAML snippet read as "the agent changed the code" (2026-11-02). When nothing changed
 * in a repository, the card says so in words.
 */
export function AgentProposal({ slug, runs, prUrl }: { slug: string; runs: readonly Run[] | undefined; prUrl?: string | null }) {
  const run = latestProposal(runs);
  if (!run) return null;
  const outputs = run.result?.outputs ?? {};
  const title = typeof outputs.title === "string" ? outputs.title : null;
  const outcome = typeof outputs.outcome === "string" ? outputs.outcome : null;
  const confidence = outputs.confidence;
  return (
    <div className="space-y-2 border-b border-line pb-3" data-testid="agent-proposal">
      <p className="text-xs text-ink-muted">
        Proposed by{" "}
        <Link href={`/p/${slug}/runs/${run.id}`}>
          {run.transition_id ?? run.stage_role}
          {run.attempt ? ` · attempt ${run.attempt}` : ""}
        </Link>
        {run.agent_slug ? ` · agent ${run.agent_slug}` : ""}
      </p>
      {(title || outcome || confidence !== undefined) && (
        <div className="flex flex-wrap items-center gap-2">
          {title && <p className="text-sm font-medium" data-donnee>{title}</p>}
          {outcome && <Badge tone="neutral">outcome: {outcome}</Badge>}
          {confidence !== undefined && confidence !== null && <Badge tone="neutral">confidence {String(confidence)}</Badge>}
        </div>
      )}
      {markdownOutputs(run).map(([name, text]) => (
        <div key={name} data-output={name}>
          <MarkdownView text={text} />
        </div>
      ))}
      {!changedTheRepository(run, prUrl) && (
        <p className="text-sm font-medium" data-testid="no-repository-change">
          No change in the repository: this is a proposal to apply. The agent committed nothing and opened no pull request.
        </p>
      )}
    </div>
  );
}
