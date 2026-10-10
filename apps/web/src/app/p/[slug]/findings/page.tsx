// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { use, useState } from "react";
import { Button, Card, Empty, ErrorNote, EtatDeLecture, Select, Table, TBody, TD, TH, THead, TR } from "@/components/ui";
import { api } from "@/lib/api";
import { relative } from "@/lib/format";

const SEVERITY_TONE: Record<string, string> = {
  critical: "text-danger font-semibold",
  high: "text-danger",
  medium: "text-warn",
  low: "text-ink-muted",
};

export default function FindingsPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<string>("");
  const findings = useQuery({
    queryKey: ["findings", slug, status],
    queryFn: () => api.findings(slug, status ? { status } : undefined),
  });
  const [error, setError] = useState<string | null>(null);

  async function act(id: string, action: string) {
    setError(null);
    try {
      await api.findingAction(id, action);
      queryClient.invalidateQueries({ queryKey: ["findings", slug] });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "action refused");
    }
  }

  return (
    <Card
      title="findings"
      action={
        <label className="flex items-center gap-2">
          <span className="text-xs text-ink-muted">filter by status</span>
          <Select
            value={status}
            onChange={(event) => setStatus(event.target.value)}
            className="w-auto"
          >
            <option value="">all</option>
            <option value="pending">to triage</option>
            <option value="created">ticket created</option>
            <option value="duplicate">duplicates</option>
            <option value="dismissed">dismissed</option>
          </Select>
        </label>
      }
    >
      {error && <ErrorNote>{error}</ErrorNote>}
      <Table>
        <THead>
          <TR>
            <TH>severity</TH>
            <TH>type</TH>
            <TH>title</TH>
            <TH>evidence</TH>
            <TH>origin</TH>
            <TH>status</TH>
            <TH>actions</TH>
          </TR>
        </THead>
        <TBody>
          {(findings.data?.items ?? []).map((finding) => (
            <TR key={finding.id}>
              <TD className={SEVERITY_TONE[finding.severity] ?? ""}>{finding.severity}</TD>
              <TD>{finding.type}</TD>
              <TD>
                {finding.title}
                {(finding.occurrences ?? 1) > 1 && (
                  <span className="ml-2 rounded bg-surface-muted px-1 text-xs">×{finding.occurrences}</span>
                )}
              </TD>
              <TD className="max-w-72 truncate font-mono text-xs text-ink-muted" title={finding.evidence}>
                {finding.evidence}
              </TD>
              <TD className="font-mono text-xs">{finding.origin_work_item_key ?? "—"}</TD>
              <TD>
                {finding.status}
                {finding.created_work_item_key && (
                  <span className="ml-1 font-mono text-xs text-ink-muted">→ {finding.created_work_item_key}</span>
                )}
                <span className="ml-2 text-xs text-ink-muted">{relative(finding.created_at)}</span>
              </TD>
              <TD className="whitespace-nowrap">
                <div className="flex gap-1">
                  {finding.status === "pending" && (
                    <Button size="sm" onClick={() => act(finding.id, "create_ticket")}>
                      create the ticket
                    </Button>
                  )}
                  {finding.status === "created" && (
                    <Button size="sm" onClick={() => act(finding.id, "agent_ready")}>
                      make agent-ready
                    </Button>
                  )}
                  <Button size="sm" onClick={() => act(finding.id, "mark_duplicate")}>
                    duplicate
                  </Button>
                  <Button size="sm" tone="danger" onClick={() => act(finding.id, "dismiss")}>
                    dismiss
                  </Button>
                </div>
              </TD>
            </TR>
          ))}
        </TBody>
      </Table>
      <EtatDeLecture lecture={findings} quoi="the findings" />
      {findings.data?.items.length === 0 && <Empty>no finding</Empty>}
    </Card>
  );
}
