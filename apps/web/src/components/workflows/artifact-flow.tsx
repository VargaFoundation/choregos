// SPDX-License-Identifier: Apache-2.0
"use client";

import { Badge, Card } from "@/components/ui";
import type { ArtifactFlow, ProcessStep } from "@/lib/types";

/** What a hovered row lights up on the map: the boxes that produce the document, and those that read it. */
export type Highlight = Map<string, "produces" | "reads">;

export function highlightOf(flow: ArtifactFlow): Highlight {
  const lit: Highlight = new Map();
  for (const id of flow.read_by) lit.set(id, "reads");
  for (const id of flow.produced_by) lit.set(id, "produces");
  return lit;
}

const ORIGINE: Record<string, string> = {
  feedback: "a person's rejection reason",
  field: "a work item field",
  missing: "nothing produces it",
};

function Steps({ ids, parId }: { ids: string[]; parId: Map<string, ProcessStep> }) {
  if (ids.length === 0) return <span className="text-ink-muted">—</span>;
  return (
    <ul className="flex flex-wrap gap-1">
      {ids.map((id) => (
        <li key={id}>
          <Badge tone="neutral" variant="soft" dot={false} title={parId.get(id)?.to_display}>
            {id}
          </Badge>
        </li>
      ))}
    </ul>
  );
}

/**
 * The artifacts of a workflow, under its map (S39-13): who produces each document and who reads it.
 * The table is the source of truth — it reads on a screen reader and a phone; the highlight on the
 * map's boxes is decoration. Nothing against an API that predates `graph.artifacts`.
 */
export function ArtifactFlowCard({
  artifacts,
  steps,
  onHighlight,
}: {
  artifacts: ArtifactFlow[] | undefined;
  steps: ProcessStep[];
  onHighlight: (lit: Highlight | null) => void;
}) {
  if (!artifacts || artifacts.length === 0) return null;
  const parId = new Map(steps.map((s) => [s.id, s]));
  return (
    <Card title="Artifacts" data-testid="artifacts-du-workflow">
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <caption className="sr-only">Artifacts</caption>
          <thead>
            <tr className="text-ink-muted">
              <th scope="col" className="py-1 pr-3 font-medium">Artifact</th>
              <th scope="col" className="py-1 pr-3 font-medium">Type</th>
              <th scope="col" className="py-1 pr-3 font-medium">Produced by</th>
              <th scope="col" className="py-1 font-medium">Read by</th>
            </tr>
          </thead>
          <tbody>
            {artifacts.map((a) => (
              <tr
                key={a.name}
                tabIndex={0}
                data-testid={`artefact-${a.name}`}
                className="border-t border-line align-top focus-visible:bg-surface-sunken hover:bg-surface-sunken"
                onMouseEnter={() => onHighlight(highlightOf(a))}
                onFocus={() => onHighlight(highlightOf(a))}
                onMouseLeave={() => onHighlight(null)}
                onBlur={() => onHighlight(null)}
              >
                <th scope="row" className="py-1.5 pr-3 font-normal">
                  <code className="text-ink">{a.name}</code>
                  <span className="mt-0.5 flex flex-wrap gap-1 text-ink-muted">
                    {a.context && <span>context of every agent step</span>}
                    {a.in_pull_request && <span>in the pull request</span>}
                  </span>
                </th>
                <td className="py-1.5 pr-3">{a.type ?? <span className="text-ink-muted">inline</span>}</td>
                <td className="py-1.5 pr-3">
                  {a.produced_by.length > 0 ? (
                    <Steps ids={a.produced_by} parId={parId} />
                  ) : (
                    <span className={a.origin === "missing" ? "text-retrying-ink" : "text-ink-muted"}>
                      {ORIGINE[a.origin] ?? "—"}
                    </span>
                  )}
                  {a.origin === "feedback" && a.produced_by.length > 0 && (
                    <span className="mt-0.5 block text-ink-muted">on rejection</span>
                  )}
                </td>
                <td className="py-1.5">
                  <Steps ids={a.read_by} parId={parId} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
