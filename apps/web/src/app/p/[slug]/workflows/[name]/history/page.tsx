// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { use, useMemo, useState } from "react";
import { Badge, Button, Card, Empty, ErrorNote, Field, Select, Table, TBody, TD, TH, THead, TR } from "@/components/ui";
import { api } from "@/lib/api";
import { diffLines, hunks } from "@/lib/diff";
import { shortDate } from "@/lib/format";
import type { WorkflowDef } from "@/lib/types";

/**
 * The published versions of a workflow (S16-13): none is overwritten, a ticket ends on its own.
 * Two versions are compared line by line; a past version is **restored** by republishing it
 * as the next one: the history grows, it is never rewritten.
 */
export default function HistoryPage({ params }: { params: Promise<{ slug: string; name: string }> }) {
  const { slug, name: brut } = use(params);
  const name = decodeURIComponent(brut);
  const client = useQueryClient();
  const versions = useQuery({
    queryKey: ["workflow-versions", slug, name],
    queryFn: () => api.workflowVersions(slug, name),
  });
  const versionList = versions.data ?? [];
  const active = versionList.find((version) => version.is_active);
  const [avant, setAvant] = useState<number | null>(null);
  const [apres, setApres] = useState<number | null>(null);
  const [aConfirmer, setAConfirmer] = useState<number | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  // By default: the version before the active one, against the active one.
  const versionAfter = apres ?? active?.version ?? versionList[0]?.version ?? null;
  const versionAvant =
    avant ?? versionList.find((version) => versionAfter !== null && version.version < versionAfter)?.version ?? null;

  async function restaurer(version: number) {
    setMessage(null);
    try {
      const publiee = await api.restoreWorkflowVersion(slug, name, version);
      setMessage(
        publiee?.version
          ? `v${version} republished as v${publiee.version}: new items are born in it, running ones finish on theirs`
          : `v${version} republished`,
      );
      await client.invalidateQueries({ queryKey: ["workflow-versions", slug, name] });
      await client.invalidateQueries({ queryKey: ["workflow", slug, name] });
      await client.invalidateQueries({ queryKey: ["workflows", slug] });
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "restoration refused");
    } finally {
      setAConfirmer(null);
    }
  }

  if (versions.error) return <ErrorNote>{String(versions.error)}</ErrorNote>;
  if (!versions.data) return <Empty>Reading the history…</Empty>;
  return (
    <div className="space-y-4">
      <Table aria-label={`Versions of ${name}`}>
        <THead>
          <TR>
            <TH>Version</TH>
            <TH>Status</TH>
            <TH>Published by</TH>
            <TH>When</TH>
            <TH>Checksum</TH>
            <TH>
              <span className="sr-only">Actions</span>
            </TH>
          </TR>
        </THead>
        <TBody>
          {versionList.map((version) => (
            <TR key={version.version}>
              <TD>v{version.version}</TD>
              <TD>
                {version.is_active ? <Badge tone="accent">Active</Badge> : <span className="text-ink-muted">Superseded</span>}
              </TD>
              <TD>{version.created_by ?? "—"}</TD>
              <TD>{version.created_at ? shortDate(version.created_at) : "—"}</TD>
              <TD>
                <code className="text-xs text-ink-muted">{version.checksum.replace("sha256:", "").slice(0, 12)}</code>
              </TD>
              <TD>
                {!version.is_active &&
                  (aConfirmer === version.version ? (
                    <span className="inline-flex items-center gap-2">
                      <Button size="sm" tone="accent" onClick={() => restaurer(version.version)}>
                        Republish v{version.version}
                      </Button>
                      <Button size="sm" onClick={() => setAConfirmer(null)}>
                        Cancel
                      </Button>
                    </span>
                  ) : (
                    <Button size="sm" onClick={() => setAConfirmer(version.version)}>
                      Restore v{version.version}
                    </Button>
                  ))}
              </TD>
            </TR>
          ))}
        </TBody>
      </Table>
      {message && (
        <p className="text-sm" role="status">
          {message}
        </p>
      )}

      {versionList.length > 1 && (
        <Card title="Compare">
          <div className="flex flex-wrap gap-4">
            <Field label="From">
              {(props) => (
                <Select {...props} value={versionAvant ?? ""} onChange={(event) => setAvant(Number(event.target.value))}>
                  {versionList.map((version) => (
                    <option key={version.version} value={version.version}>
                      v{version.version}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
            <Field label="To">
              {(props) => (
                <Select {...props} value={versionAfter ?? ""} onChange={(event) => setApres(Number(event.target.value))}>
                  {versionList.map((version) => (
                    <option key={version.version} value={version.version}>
                      v{version.version}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          </div>
          <Diff
            avant={versionList.find((version) => version.version === versionAvant)}
            apres={versionList.find((version) => version.version === versionAfter)}
          />
        </Card>
      )}
    </div>
  );
}

/** The lines that change between two versions, with two lines of context around them. */
function Diff({ avant, apres }: { avant?: WorkflowDef; apres?: WorkflowDef }) {
  const morceaux = useMemo(
    () => (avant && apres ? hunks(diffLines(avant.yaml, apres.yaml)) : []),
    [avant, apres],
  );
  if (!avant || !apres) return null;
  if (morceaux.length === 0) return <p className="mt-3 text-sm text-ink-muted">v{avant.version} and v{apres.version} are identical.</p>;
  return (
    <div className="mt-3 space-y-3" data-testid="workflow-diff" aria-label={`Changes from v${avant.version} to v${apres.version}`}>
      {morceaux.map((morceau, index) => (
        <pre key={index} className="overflow-hidden whitespace-pre-wrap break-all rounded border border-line bg-surface p-2 text-xs">
          {morceau.map((row, rang) => (
            <div
              key={rang}
              // The text stays in ink: the colour is only a marker in the margin (AA contrast), the
              // sign and the screen-reader text carry the meaning.
              className={
                row.kind === "add"
                  ? "border-l-2 border-ok bg-ok/10 pl-1 text-ink"
                  : row.kind === "del"
                    ? "border-l-2 border-danger bg-danger/10 pl-1 text-ink"
                    : "pl-1.5 text-ink-muted"
              }
            >
              <span aria-hidden>{row.kind === "add" ? "+ " : row.kind === "del" ? "- " : "  "}</span>
              <span className="sr-only">{row.kind === "add" ? "added: " : row.kind === "del" ? "removed: " : ""}</span>
              {row.text}
            </div>
          ))}
        </pre>
      ))}
    </div>
  );
}
