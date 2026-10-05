// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { use } from "react";
import { Badge, TBody, TD, TH, THead, TR, Table } from "@varga/design-system";
import { Empty, ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";
import { shortDate } from "@/lib/format";

/** Les versions publiées d'un workflow : aucune n'est écrasée, et un ticket finit sur la sienne. */
export default function HistoryPage({
  params,
}: {
  params: Promise<{ slug: string; name: string }>;
}) {
  const { slug, name: brut } = use(params);
  const name = decodeURIComponent(brut);
  const versions = useQuery({
    queryKey: ["workflow-versions", slug, name],
    queryFn: () => api.workflowVersions(slug, name),
  });
  if (versions.error) return <ErrorNote>{String(versions.error)}</ErrorNote>;
  if (!versions.data) return <Empty>reading the history…</Empty>;
  return (
    <Table aria-label={`versions of ${name}`}>
      <THead>
        <TR>
          <TH>version</TH>
          <TH>status</TH>
          <TH>published by</TH>
          <TH>when</TH>
          <TH>checksum</TH>
        </TR>
      </THead>
      <TBody>
        {versions.data.map((version) => (
          <TR key={version.version}>
            <TD>v{version.version}</TD>
            <TD>
              {version.is_active ? (
                <Badge tone="accent">active</Badge>
              ) : (
                <span className="text-ink-muted">superseded</span>
              )}
            </TD>
            <TD>{version.created_by ?? "—"}</TD>
            <TD>{version.created_at ? shortDate(version.created_at) : "—"}</TD>
            <TD>
              <code className="text-xs text-ink-muted">
                {version.checksum.replace("sha256:", "").slice(0, 12)}
              </code>
            </TD>
          </TR>
        ))}
      </TBody>
    </Table>
  );
}
