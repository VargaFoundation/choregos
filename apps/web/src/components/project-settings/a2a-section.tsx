// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { SettingsSection } from "@/components/project-settings/section";
import { Button, Card, ErrorNote, Field, Input } from "@/components/ui";
import { api } from "@/lib/api";
import {
  A2A_MAX_FILE_BYTES_CEILING,
  A2A_MAX_FILE_BYTES_DEFAULT,
  a2aFileLimit,
  formatMiB,
} from "@/lib/integrations";
import { useProjectConfigPatch } from "@/lib/project-config";
import type { ProjectDto } from "@/lib/types";

const MIB = 1024 * 1024;

/** What an outside agent may send this project's skills over A2A: the files of one message. */
export function A2aSection({ slug }: { slug: string }) {
  const project = useQuery({ queryKey: ["project", slug], queryFn: () => api.project(slug) });
  return (
    <SettingsSection
      id="a2a"
      title="Another agent, over A2A"
      lead="The files an outside agent may attach to a message for this project's skills."
    >
      {project.data && <FileLimitForm key={project.data.id} slug={slug} project={project.data} />}
    </SettingsSection>
  );
}

/** The limit in MiB as typed, or a reason it cannot be saved. */
export function parseMiB(text: string): { bytes: number } | { error: string } {
  const value = Number(text.trim().replace(",", "."));
  if (!text.trim() || !Number.isFinite(value) || value <= 0) return { error: "A positive number of MiB" };
  const bytes = Math.round(value * MIB);
  if (bytes > A2A_MAX_FILE_BYTES_CEILING) return { error: `At most ${formatMiB(A2A_MAX_FILE_BYTES_CEILING)}` };
  return { bytes };
}

function FileLimitForm({ slug, project }: { slug: string; project: ProjectDto }) {
  const current = a2aFileLimit(project.config);
  const { patch, pending, message, error } = useProjectConfigPatch(slug);
  const [text, setText] = useState(String(current / MIB));
  const parsed = parseMiB(text);
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (!("bytes" in parsed)) return;
        const bytes = parsed.bytes;
        void patch(
          (latest) => ({
            // The default is not written down: a project that never chose follows it.
            a2a: bytes === A2A_MAX_FILE_BYTES_DEFAULT ? undefined : { ...latest.a2a, max_file_bytes: bytes },
          }),
          { saved: "File limit saved" },
        );
      }}
    >
      <Card title="Files in a message">
        <Field
          label="Largest file a message may carry (MiB)"
          hint={`All the files of one message together stay under it too. ${formatMiB(A2A_MAX_FILE_BYTES_DEFAULT)} unless you change it, ${formatMiB(A2A_MAX_FILE_BYTES_CEILING)} at most. A file must also be of a type the workflow lists.`}
          error={"error" in parsed ? parsed.error : undefined}
        >
          {(p) => (
            <Input
              {...p}
              inputMode="decimal"
              value={text}
              data-testid="a2a-max-file-mib"
              onChange={(e) => setText(e.target.value)}
            />
          )}
        </Field>
        <div className="mt-3 flex flex-wrap items-center justify-end gap-3">
          {message && (
            <p className="text-sm text-ok" role="status">
              {message}
            </p>
          )}
          {error && <ErrorNote>{error}</ErrorNote>}
          <Button tone="primary" type="submit" disabled={pending || "error" in parsed}>
            Save the file limit
          </Button>
        </div>
      </Card>
    </form>
  );
}
