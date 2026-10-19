// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Badge, Card, Empty, EtatDeLecture, Select, TBody, TD, TH, THead, TR, Table } from "@/components/ui";
import type { Tone } from "@/components/ui";
import { api } from "@/lib/api";
import { relative } from "@/lib/format";

const LAYERS: Record<string, string> = {
  proxy: "Egress proxy",
  runner: "Runner",
  broker: "Tool broker",
  platform: "Platform",
};

const TONES: Record<string, Tone> = { allowed: "ok", denied: "danger", rate_limited: "waiting", unreachable: "neutral" };
const WORDS: Record<string, string> = { allowed: "Allowed", denied: "Refused", rate_limited: "Slowed down", unreachable: "Unreachable" };

/**
 * What reached the network and what was refused, per host (ADR 0070): the egress proxy, the runner,
 * the tool broker. Refusals first by default — that is what an administrator opens it for.
 */
export function NetworkActivityCard({ org }: { org: string }) {
  const [refusedOnly, setRefusedOnly] = useState(true);
  const [since, setSince] = useState("24h");
  const hours = since === "7d" ? 24 * 7 : 24;
  const activity = useQuery({
    queryKey: ["network-activity", org, since],
    queryFn: () => api.networkActivity(org, { since: new Date(Date.now() - hours * 3_600_000).toISOString() }),
    retry: false,
  });
  const rows = (activity.data?.rows ?? []).filter((row) => !refusedOnly || row.decision !== "allowed");
  return (
    <Card title="Network activity" className="lg:col-span-2">
      <div className="space-y-3 text-sm">
        <div className="flex flex-wrap items-center gap-4">
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={refusedOnly} onChange={(e) => setRefusedOnly(e.target.checked)} />
            Refused only
          </label>
          <label className="flex items-center gap-2">
            <span className="text-xs text-ink-muted">Period</span>
            <Select aria-label="Period" value={since} onChange={(e) => setSince(e.target.value)} className="w-auto">
              <option value="24h">Last 24 hours</option>
              <option value="7d">Last 7 days</option>
            </Select>
          </label>
        </div>
        <EtatDeLecture lecture={activity} quoi="the network activity" />
        {activity.data && rows.length === 0 && (
          <Empty>{refusedOnly ? "Nothing was refused in this period." : "No network activity in this period."}</Empty>
        )}
        {rows.length > 0 && (
          <div className="overflow-x-auto">
            <Table>
              <THead>
                <TR>
                  <TH>Host</TH>
                  <TH>Where</TH>
                  <TH>Decision</TH>
                  <TH>Reason</TH>
                  <TH align="right">Count</TH>
                  <TH align="right">Runs</TH>
                  <TH>Projects</TH>
                  <TH>Last seen</TH>
                </TR>
              </THead>
              <TBody>
                {rows.map((row) => (
                  <TR key={`${row.host}|${row.layer}|${row.decision}|${row.reason}`}>
                    <TD className="font-mono text-xs">{row.host}</TD>
                    <TD>{LAYERS[row.layer] ?? row.layer}</TD>
                    <TD>
                      <Badge tone={TONES[row.decision] ?? "neutral"}>{WORDS[row.decision] ?? row.decision}</Badge>
                    </TD>
                    <TD className="text-xs text-ink-muted">{row.reason || "—"}</TD>
                    <TD align="right">{row.count}</TD>
                    <TD align="right">{row.runs}</TD>
                    <TD className="text-xs">{row.projects.join(", ") || "—"}</TD>
                    <TD className="text-xs">{relative(row.last_at)}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </div>
        )}
      </div>
    </Card>
  );
}
