// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { Badge, Card, Empty, ErrorNote, TBody, TD, TH, THead, TR, Table } from "@/components/ui";
import { api } from "@/lib/api";
import { shortDate, usd } from "@/lib/format";

/**
 * The keys the model gateway minted for runs: which run and project each serves, what it spent of
 * its budget, and whether it is revoked. The secret itself is never shown nor asked for.
 * Platform administrators only: the API refuses the others.
 */
export function GatewayKeysCard() {
  const keys = useQuery({ queryKey: ["gateway-keys"], queryFn: () => api.gatewayKeys() });
  return (
    <Card title="Gateway keys">
      {keys.isError ? (
        <ErrorNote>{keys.error instanceof Error ? keys.error.message : "Refused"}</ErrorNote>
      ) : !keys.data ? (
        <p className="text-sm text-ink-muted">Reading the keys…</p>
      ) : keys.data.length === 0 ? (
        <Empty>No gateway key</Empty>
      ) : (
        <Table>
          <THead>
            <TR>
              <TH>Key</TH>
              <TH>Run</TH>
              <TH>Project</TH>
              <TH>Spent / budget</TH>
              <TH>Expires</TH>
              <TH>State</TH>
              <TH>Created</TH>
            </TR>
          </THead>
          <TBody>
            {keys.data.map((key) => (
              <TR key={key.key_id}>
                <TD className="font-mono text-xs">{key.key_id}</TD>
                <TD className="font-mono text-xs">{key.run_id ?? "—"}</TD>
                <TD>{key.project_slug ?? "—"}</TD>
                <TD>
                  {usd(key.spend_usd ?? 0)} / {key.budget_usd == null ? "no budget" : usd(key.budget_usd)}
                </TD>
                <TD>{shortDate(key.expires_at)}</TD>
                <TD>{key.revoked ? <Badge tone="danger">Revoked</Badge> : <Badge tone="ok">Active</Badge>}</TD>
                <TD>{shortDate(key.created_at)}</TD>
              </TR>
            ))}
          </TBody>
        </Table>
      )}
    </Card>
  );
}
