// SPDX-License-Identifier: Apache-2.0
"use client";

import { LevelBadge } from "@/components/level-badge";
import { Provenance } from "@/components/provenance";
import { type ArtifactGroup, titleOf } from "@/lib/artifacts";
import type { Level } from "@/lib/levels";
import type { Artifact } from "@/lib/types";

const UNIT_SIGN: Record<string, string> = { percent: "%", ratio: "" };

/** A metric's number and its unit, as a person writes them. */
export function metricText(value: number | string, unit?: string | null): string {
  if (!unit) return String(value);
  const sign = UNIT_SIGN[unit];
  if (sign !== undefined) return `${value}${sign}`;
  return `${value} ${unit}`;
}

/** A number an agent reports is its own account, and the screen says so (ADR 0129). */
function isDeclared(artifact: Artifact): boolean {
  return Boolean(artifact.produced_by.agent);
}

/** One artifact that fits on a line: a level, a metric, or what a diff changes. */
export function ArtifactValue({ artifact, onOpen }: { artifact: Artifact; onOpen?: () => void }) {
  const display = artifact.display;
  if (display?.type === "level" && artifact.value !== null && artifact.value !== undefined) {
    return (
      <LevelBadge value={String(artifact.value)} levels={(display.levels ?? []) as Level[]} data-testid={`artifact-value-${artifact.name}`} />
    );
  }
  if (display?.type === "metric" && artifact.value !== null && artifact.value !== undefined) {
    return (
      <span data-testid={`artifact-value-${artifact.name}`} data-verbatim>
        <span className="font-medium tabular-nums">{metricText(artifact.value, display.unit)}</span>
        {isDeclared(artifact) && <Provenance de="declared" className="ml-1.5" />}
      </span>
    );
  }
  if (display?.type === "diff" && display.diff) {
    const { files, additions, deletions, withheld } = display.diff;
    const body = (
      <>
        <span className="text-succeeded-ink">+{additions}</span> <span className="text-failed-ink">−{deletions}</span>
        <span className="text-ink-muted">
          {" "}
          · {files} {files === 1 ? "file" : "files"}
          {withheld?.length ? " · patch withheld" : ""}
        </span>
      </>
    );
    return onOpen ? (
      <button type="button" onClick={onOpen} className="min-h-6 text-left underline-offset-2 hover:underline" data-testid={`artifact-value-${artifact.name}`}>
        {body}
      </button>
    ) : (
      <span data-testid={`artifact-value-${artifact.name}`}>{body}</span>
    );
  }
  return null;
}

/** The facts of a workflow, as rows of the *About this ticket* list. */
export function ArtifactFacts({ facts, onOpen }: { facts: ArtifactGroup[]; onOpen: (name: string) => void }) {
  return (
    <>
      {facts.map((group) => (
        <div key={group.name} className="contents" data-testid={`ticket-fact-${group.name}`}>
          <dt className="text-ink-muted" data-verbatim>
            {titleOf(group)}
          </dt>
          <dd>
            <ArtifactValue artifact={group.latest} onOpen={() => onOpen(group.name)} />
          </dd>
        </div>
      ))}
    </>
  );
}
