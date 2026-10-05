// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { Card, Empty, StateBadge } from "@/components/ui";
import { api } from "@/lib/api";

/** Ce que la plateforme sait faire tourner : les backends d'agent et les exécuteurs déclarés. */
export default function PlatformPage() {
  const backends = useQuery({ queryKey: ["backends"], queryFn: () => api.backends() });
  const executors = useQuery({ queryKey: ["executors"], queryFn: () => api.executors() });
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="agent backends">
        <ul className="space-y-1 text-xs">
          {(backends.data ?? []).map((backend) => (
            <li key={backend.name} className="flex items-center gap-2">
              <StateBadge
                state={backend.enabled ? "ok" : "disabled"}
                display={backend.enabled ? "enabled" : "disabled"}
                kind={backend.enabled ? "terminal" : "blocked"}
              />
              <span className="font-mono">{backend.name}</span>
              <span className="text-ink-muted">{backend.version ?? ""}</span>
            </li>
          ))}
        </ul>
        {(backends.data ?? []).length === 0 && <Empty>no backend registered</Empty>}
      </Card>
      <Card title="executors">
        <ul className="space-y-1 text-xs">
          {(executors.data ?? []).map((executor) => (
            <li key={executor.kind} className="flex items-center gap-2">
              <span className="font-mono">{executor.kind}</span>
              <span className="text-ink-muted">{executor.enabled === false ? "disabled" : "enabled"}</span>
            </li>
          ))}
        </ul>
        {(executors.data ?? []).length === 0 && <Empty>no executor registered</Empty>}
      </Card>
    </div>
  );
}
