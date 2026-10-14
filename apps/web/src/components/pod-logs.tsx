// SPDX-License-Identifier: Apache-2.0
/**
 * The runner pod's log tail and how the pod ended (S36-02, S36-03). What the ACP journal cannot
 * say: a pod that died before the agent spoke — an image that does not pull, a CLI that crashes,
 * an OOM kill. Lines arrive redacted from the API; the console shows them as they are.
 */
"use client";

import { Badge, Empty, LEGENDE } from "@/components/ui";
import { shortDate } from "@/lib/format";
import type { RunLogs } from "@/lib/types";

export function PodLogs({ logs }: { logs: RunLogs | undefined }) {
  if (!logs) return <Empty>loading…</Empty>;
  if (logs.source === "none") {
    return (
      <Empty>
        no pod log captured yet — the tail appears once the pod runs, and is archived when the run ends
      </Empty>
    );
  }
  const lines = logs.lines ?? [];
  const ended = logs.source === "archive";
  return (
    <div className="space-y-3" data-testid="pod-logs" data-source={logs.source}>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Badge tone={ended ? "neutral" : "running"} data-testid="pod-logs-source">
          {ended ? "archived" : "live"}
        </Badge>
        <span className="font-mono text-ink-muted">state {logs.state}</span>
        {logs.exit_code !== null && logs.exit_code !== undefined && (
          <span className="font-mono text-ink-muted" data-testid="pod-exit-code">
            exit {logs.exit_code}
          </span>
        )}
        {logs.reason && (
          <Badge tone={logs.state === "failed" ? "danger" : "neutral"} data-testid="pod-reason">
            {logs.reason}
          </Badge>
        )}
        {logs.captured_at && (
          <span className="text-ink-muted">
            {ended ? "archived" : "captured"} {shortDate(logs.captured_at)}
          </span>
        )}
      </div>
      {logs.message && <p className="text-sm">{logs.message}</p>}
      {logs.error && (
        <p className="text-sm text-danger" role="status">
          {logs.error}
        </p>
      )}
      {lines.length === 0 ? (
        <Empty>the pod wrote nothing</Empty>
      ) : (
        <div>
          <p className={LEGENDE}>
            {logs.truncated ? `last ${lines.length} lines` : `${lines.length} line${lines.length > 1 ? "s" : ""}`} · secrets
            masked
          </p>
          <pre
            className="raised mt-1 max-h-[480px] overflow-auto border border-line p-3 font-mono text-xs whitespace-pre-wrap break-all"
            data-testid="pod-log-lines"
          >
            {lines.join("\n")}
          </pre>
        </div>
      )}
    </div>
  );
}
