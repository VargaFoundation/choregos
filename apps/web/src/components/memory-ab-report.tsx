// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Card, ErrorNote, Select } from "@/components/ui";
import { api } from "@/lib/api";
import { percent, usd } from "@/lib/format";
import type { MemoryAbGroup } from "@/lib/types";

const WEEKS = [4, 12, 26] as const;

/**
 * Do tickets go better with a memory? The organisation's tickets over the last weeks, split into the
 * projects whose runs read a memory and the ones whose runs do not, with the report's own verdict.
 */
export function MemoryAbReportCard({ org }: { org: string }) {
  const [weeks, setWeeks] = useState<number>(4);
  const report = useQuery({ queryKey: ["memory-ab-report", org, weeks], queryFn: () => api.memoryAbReport(org, weeks) });
  return (
    <Card
      title="Does memory help?"
      action={
        <label className="flex items-center gap-2 text-xs text-ink-muted">
          Period
          <Select value={String(weeks)} onChange={(e) => setWeeks(Number(e.target.value))}>
            {WEEKS.map((w) => (
              <option key={w} value={String(w)}>
                {w} weeks
              </option>
            ))}
          </Select>
        </label>
      }
    >
      {report.isError ? (
        <ErrorNote>{report.error instanceof Error ? report.error.message : "Refused"}</ErrorNote>
      ) : !report.data ? (
        <p className="text-sm text-ink-muted">Reading the report…</p>
      ) : (
        <div className="space-y-3">
          <p className="text-sm font-medium text-ink" data-testid="ab-verdict">
            {report.data.verdict}
          </p>
          {report.data.detail && <p className="text-sm text-ink-muted">{report.data.detail}</p>}
          <div className="grid gap-3 sm:grid-cols-2">
            <Group title="With a memory" group={report.data.with_memory} />
            <Group title="Without a memory" group={report.data.without_memory} />
          </div>
        </div>
      )}
    </Card>
  );
}

function Group({ title, group }: { title: string; group?: MemoryAbGroup }) {
  return (
    <section aria-label={title} className="space-y-1 rounded-md border border-line p-3 text-sm">
      <h3 className="text-xs font-semibold text-ink">{title}</h3>
      {group ? (
        <dl className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 text-xs">
          <dt className="text-ink-muted">Tickets</dt>
          <dd>{group.tickets ?? 0}</dd>
          <dt className="text-ink-muted">Merged at first pass</dt>
          <dd>{group.first_pass_merge_rate === undefined ? "—" : percent(group.first_pass_merge_rate)}</dd>
          <dt className="text-ink-muted">Cost per ticket</dt>
          <dd>{usd(group.cost_per_ticket_usd)}</dd>
          <dt className="text-ink-muted">Projects</dt>
          <dd>{group.projects?.length ? group.projects.join(", ") : "—"}</dd>
        </dl>
      ) : (
        <p className="text-xs text-ink-muted">No data</p>
      )}
    </section>
  );
}
