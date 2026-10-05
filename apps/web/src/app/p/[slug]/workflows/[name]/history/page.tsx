// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { use, useMemo, useState } from "react";
import { Badge, Field, Select, TBody, TD, TH, THead, TR, Table } from "@varga/design-system";
import { Button, Card, Empty, ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";
import { diffLines, hunks } from "@/lib/diff";
import { shortDate } from "@/lib/format";
import type { WorkflowDef } from "@/lib/types";

/**
 * Les versions publiées d'un workflow (S16-13) : aucune n'est écrasée, un ticket finit sur la sienne.
 * Deux versions se comparent ligne à ligne ; une version passée se **restaure** en la republiant
 * comme la suivante — l'historique s'allonge, il ne se réécrit pas.
 */
export default function HistoryPage({ params }: { params: Promise<{ slug: string; name: string }> }) {
  const { slug, name: brut } = use(params);
  const name = decodeURIComponent(brut);
  const client = useQueryClient();
  const versions = useQuery({
    queryKey: ["workflow-versions", slug, name],
    queryFn: () => api.workflowVersions(slug, name),
  });
  const liste = versions.data ?? [];
  const active = liste.find((version) => version.is_active);
  const [avant, setAvant] = useState<number | null>(null);
  const [apres, setApres] = useState<number | null>(null);
  const [aConfirmer, setAConfirmer] = useState<number | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  // Par défaut : la version qui précède l'active, contre l'active.
  const versionApres = apres ?? active?.version ?? liste[0]?.version ?? null;
  const versionAvant =
    avant ?? liste.find((version) => versionApres !== null && version.version < versionApres)?.version ?? null;

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
  if (!versions.data) return <Empty>reading the history…</Empty>;
  return (
    <div className="space-y-4">
      <Table aria-label={`versions of ${name}`}>
        <THead>
          <TR>
            <TH>version</TH>
            <TH>status</TH>
            <TH>published by</TH>
            <TH>when</TH>
            <TH>checksum</TH>
            <TH>
              <span className="sr-only">actions</span>
            </TH>
          </TR>
        </THead>
        <TBody>
          {liste.map((version) => (
            <TR key={version.version}>
              <TD>v{version.version}</TD>
              <TD>
                {version.is_active ? <Badge tone="accent">active</Badge> : <span className="text-ink-muted">superseded</span>}
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
                        republish v{version.version}
                      </Button>
                      <Button size="sm" onClick={() => setAConfirmer(null)}>
                        cancel
                      </Button>
                    </span>
                  ) : (
                    <Button size="sm" onClick={() => setAConfirmer(version.version)}>
                      restore v{version.version}
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

      {liste.length > 1 && (
        <Card title="compare">
          <div className="flex flex-wrap gap-4">
            <Field label="from">
              {(props) => (
                <Select {...props} value={versionAvant ?? ""} onChange={(event) => setAvant(Number(event.target.value))}>
                  {liste.map((version) => (
                    <option key={version.version} value={version.version}>
                      v{version.version}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
            <Field label="to">
              {(props) => (
                <Select {...props} value={versionApres ?? ""} onChange={(event) => setApres(Number(event.target.value))}>
                  {liste.map((version) => (
                    <option key={version.version} value={version.version}>
                      v{version.version}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          </div>
          <Diff
            avant={liste.find((version) => version.version === versionAvant)}
            apres={liste.find((version) => version.version === versionApres)}
          />
        </Card>
      )}
    </div>
  );
}

/** Les lignes qui changent entre deux versions, avec deux lignes de contexte autour. */
function Diff({ avant, apres }: { avant?: WorkflowDef; apres?: WorkflowDef }) {
  const morceaux = useMemo(
    () => (avant && apres ? hunks(diffLines(avant.yaml, apres.yaml)) : []),
    [avant, apres],
  );
  if (!avant || !apres) return null;
  if (morceaux.length === 0) return <p className="mt-3 text-sm text-ink-muted">v{avant.version} and v{apres.version} are identical.</p>;
  return (
    <div className="mt-3 space-y-3" data-testid="workflow-diff" aria-label={`changes from v${avant.version} to v${apres.version}`}>
      {morceaux.map((morceau, index) => (
        <pre key={index} className="overflow-hidden whitespace-pre-wrap break-all rounded border border-line bg-surface p-2 text-xs">
          {morceau.map((ligne, rang) => (
            <div
              key={rang}
              className={ligne.kind === "add" ? "bg-ok/10 text-ok" : ligne.kind === "del" ? "bg-danger/10 text-danger" : "text-ink-muted"}
            >
              <span aria-hidden>{ligne.kind === "add" ? "+ " : ligne.kind === "del" ? "- " : "  "}</span>
              <span className="sr-only">{ligne.kind === "add" ? "added: " : ligne.kind === "del" ? "removed: " : ""}</span>
              {ligne.text}
            </div>
          ))}
        </pre>
      ))}
    </div>
  );
}
