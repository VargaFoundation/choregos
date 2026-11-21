// SPDX-License-Identifier: Apache-2.0
"use client";

import { useState } from "react";
import { AttachmentsField } from "@/components/workflows/attachments-field";
import { Button, Empty, ErrorNote } from "@/components/ui";
import { api, artifactDownloadUrl } from "@/lib/api";
import { humanSize } from "@/lib/artifacts";
import { type AttachmentRule, handedFiles } from "@/lib/attachments";
import { shortDate } from "@/lib/format";
import type { Artifact } from "@/lib/types";

/** Who handed a file, in words (ADR 0201 §2). */
function handedBy(artifact: Artifact): string {
  return artifact.handed_by === "a2a" ? "an application" : "a person";
}

/**
 * The files handed to a work item (ADR 0201): name, type, size, who handed each and when, a download
 * through the content route, and "Add files" while the work item is not terminal.
 */
export function FilesCard({
  itemId,
  artifacts,
  rule,
  terminal,
  onChanged,
}: {
  itemId: string;
  artifacts: Artifact[];
  /** What the workflow accepts; null when it declares nothing (files an application sent are still listed). */
  rule: AttachmentRule | null;
  terminal: boolean;
  onChanged: () => void;
}) {
  const files = handedFiles(artifacts);
  const [adding, setAdding] = useState(false);
  const [chosen, setChosen] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const open = !terminal && rule !== null;

  async function send() {
    setBusy(true);
    setError(null);
    try {
      await api.attachWorkItemFiles(itemId, chosen);
      setChosen([]);
      setAdding(false);
      onChanged();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "request refused");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3 text-sm" data-testid="files-card">
      {files.length === 0 ? (
        <Empty>no file handed to this work item yet.</Empty>
      ) : (
        <ul className="divide-y divide-line" data-testid="handed-files">
          {files.map((file) => (
            <li key={file.name} className="flex flex-wrap items-center gap-x-4 gap-y-1 py-2">
              <a
                className="break-all font-mono text-accent underline"
                href={artifactDownloadUrl(itemId, `${file.name}@${file.version}`)}
                download
                data-verbatim
              >
                {file.name}
              </a>
              <span className="text-xs text-ink-muted">
                {file.type} · {humanSize(file.size)} · handed by {handedBy(file)}
                {file.created_at ? ` · ${shortDate(file.created_at)}` : ""}
                {file.version > 1 ? ` · revision ${file.version}` : ""}
              </span>
            </li>
          ))}
        </ul>
      )}
      {open &&
        (adding ? (
          <div className="space-y-2">
            <AttachmentsField rule={rule} files={chosen} onChange={setChosen} disabled={busy} />
            <div className="flex gap-2">
              <Button tone="primary" size="sm" disabled={busy || chosen.length === 0} onClick={() => void send()}>
                Send the files
              </Button>
              <Button
                size="sm"
                disabled={busy}
                onClick={() => {
                  setAdding(false);
                  setChosen([]);
                  setError(null);
                }}
              >
                Cancel
              </Button>
            </div>
          </div>
        ) : (
          <Button size="sm" onClick={() => setAdding(true)}>
            Add files
          </Button>
        ))}
      {error && <ErrorNote>{error}</ErrorNote>}
    </div>
  );
}
