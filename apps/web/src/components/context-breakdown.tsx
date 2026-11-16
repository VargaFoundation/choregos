// SPDX-License-Identifier: Apache-2.0
"use client";

import { Provenance } from "@/components/provenance";
import { Card, Empty, Label, Table, TBody, TD, TH, THead, TR } from "@/components/ui";
import { contextView, percent } from "@/lib/context-breakdown";
import { tokens } from "@/lib/format";
import type { RunContext } from "@/lib/types";
import { HUE_CLASSES } from "@/lib/workflow-hue";
import { cn } from "@/lib/cn";

/**
 * What fills the run's context (ADR 0141 §6), as Claude Code's `/context` says it for a session: the
 * last model call's sections against the model's window, and the free space. The total is the model's
 * count; the split is the platform's estimate, scaled to it — the card says so.
 */
export function RunContextCard({ context }: { context: RunContext | undefined }) {
  if (!context) return null;
  if (!context.measurable) {
    return (
      <Card title="Context" action={<Provenance de="observed" />}>
        <p className="text-sm text-ink-muted" data-testid="context-not-measurable">
          Not measurable for this backend{context.backend ? ` (${context.backend})` : ""}: the platform does not see its
          model calls.
        </p>
      </Card>
    );
  }
  if (!context.latest) {
    return (
      <Card title="Context" action={<Provenance de="observed" />}>
        <Empty>No model call measured yet.</Empty>
      </Card>
    );
  }
  const view = contextView(context.latest);
  return (
    <Card title="Context" action={<Provenance de="observed" />}>
      <div className="space-y-3" data-testid="run-context">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <p className="text-sm">
            <span className="text-lg">{tokens(view.total)}</span> tokens
            {view.window !== null ? ` of ${tokens(view.window)}` : ""} · call {context.latest.call} ·{" "}
            <span className="font-mono text-xs" data-verbatim>
              {context.latest.model}
            </span>
          </p>
          <p className="text-xs text-ink-muted" data-testid="context-free">
            {view.free === null ? "Free space unknown: the catalogue does not know this model's window" : `${tokens(view.free)} free`}
          </p>
        </div>
        <div
          className="flex h-3 w-full overflow-hidden rounded-sm border border-line bg-surface-sunken"
          role="img"
          aria-label={`Context: ${view.rows.map((row) => `${row.label} ${percent(row.share)}`).join(", ")}${
            view.freeShare === null ? ", free space unknown" : `, free ${percent(view.freeShare)}`
          }`}
          data-testid="context-bar"
        >
          {view.rows.map((row) => (
            <div
              key={row.key}
              className={HUE_CLASSES[row.hue].solid}
              style={{ width: `${row.share * 100}%` }}
              data-section={row.key}
              title={`${row.label}: ${tokens(row.tokens)}`}
            />
          ))}
        </div>
        <Table>
          <THead>
            <tr>
              <TH>Section</TH>
              <TH align="right">Tokens</TH>
              <TH align="right">{view.window !== null ? "Of the window" : "Of the total"}</TH>
            </tr>
          </THead>
          <TBody>
            {view.rows.map((row) => (
              <TR key={row.key} data-testid={`context-row-${row.key}`}>
                <TD>
                  <span className="flex items-center gap-2">
                    <span aria-hidden className={cn("inline-block size-2.5 rounded-xs", HUE_CLASSES[row.hue].solid)} />
                    {row.label}
                  </span>
                </TD>
                <TD align="right">{row.tokens.toLocaleString("en")}</TD>
                <TD align="right">{percent(row.share)}</TD>
              </TR>
            ))}
            <TR data-testid="context-row-free">
              <TD>
                <span className="flex items-center gap-2">
                  <span aria-hidden className="inline-block size-2.5 rounded-xs border border-line bg-surface-sunken" />
                  Free space
                </span>
              </TD>
              <TD align="right">{view.free === null ? "unknown" : view.free.toLocaleString("en")}</TD>
              <TD align="right">{view.freeShare === null ? "—" : percent(view.freeShare)}</TD>
            </TR>
          </TBody>
        </Table>
        <Label data-testid="context-note">
          Split estimated, total exact: the model counted {view.total.toLocaleString("en")} input tokens; the platform
          estimates each section and scales them to that count.
          {context.series.length > 1 ? ` ${context.series.length} calls kept for this run.` : ""}
        </Label>
      </div>
    </Card>
  );
}
