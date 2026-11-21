// SPDX-License-Identifier: Apache-2.0
"use client";

import { useState } from "react";
import { Button } from "@/components/ui";
import { humanSize } from "@/lib/artifacts";
import { type AttachmentRule, acceptAttribute, acceptedSentence, fileRefusal } from "@/lib/attachments";

/**
 * The files a person hands with a work item (ADR 0201): a drop zone that states what the workflow
 * accepts, the files chosen so far, and a courtesy check before the send. The API is the judge.
 */
export function AttachmentsField({
  rule,
  files,
  onChange,
  disabled,
  label = "Files",
}: {
  rule: AttachmentRule;
  files: File[];
  onChange: (files: File[]) => void;
  disabled?: boolean;
  label?: string;
}) {
  const [refusals, setRefusals] = useState<string[]>([]);

  function consider(given: File[]) {
    const refused: string[] = [];
    const next = [...files];
    for (const file of given) {
      const why = fileRefusal(file.name, file.size, rule);
      if (why) refused.push(why);
      else if (!next.some((kept) => kept.name === file.name && kept.size === file.size)) {
        if (next.length >= rule.max) refused.push(`${file.name}: at most ${rule.max} files`);
        else next.push(file);
      }
    }
    setRefusals(refused);
    onChange(next);
  }

  return (
    <div className="space-y-2" data-testid="attachments-field">
      {/* Dropping is a pointer shortcut; the file input inside is the keyboard path. */}
      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions */}
      <div
        className="space-y-2 rounded-control border border-dashed border-line p-3"
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => {
          event.preventDefault();
          if (!disabled) consider(Array.from(event.dataTransfer.files));
        }}
        role="group"
        aria-label="Drop zone for files"
        data-testid="attachments-dropzone"
      >
        <p className="text-ink-muted">Drop files here, or choose them.</p>
        <input
          type="file"
          multiple
          aria-label={label}
          accept={acceptAttribute(rule)}
          disabled={disabled}
          className="text-xs"
          onChange={(event) => {
            const chosen = Array.from(event.target.files ?? []);
            event.target.value = "";
            consider(chosen);
          }}
        />
        <p className="text-xs text-ink-muted" data-testid="attachments-rule">
          {acceptedSentence(rule)}
        </p>
      </div>
      {files.length > 0 && (
        <ul className="space-y-1 text-xs" data-testid="attachments-chosen">
          {files.map((file) => (
            <li key={`${file.name}:${file.size}`} className="flex items-center gap-2">
              <span className="break-all font-mono" data-verbatim>
                {file.name}
              </span>
              <span className="text-ink-muted">{humanSize(file.size)}</span>
              <Button
                size="sm"
                type="button"
                disabled={disabled}
                aria-label={`Remove ${file.name}`}
                onClick={() => onChange(files.filter((kept) => kept !== file))}
              >
                Remove
              </Button>
            </li>
          ))}
        </ul>
      )}
      <p className="text-xs text-ink-muted" data-testid="attachments-count">
        {files.length} of {rule.min > 0 ? `${rule.min} required` : "none required"}, {rule.max} at most
      </p>
      {refusals.map((why) => (
        <p key={why} className="text-xs text-danger" role="alert">
          {why}
        </p>
      ))}
    </div>
  );
}
