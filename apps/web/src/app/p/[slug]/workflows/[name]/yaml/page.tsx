// SPDX-License-Identifier: Apache-2.0
"use client";

import dynamic from "next/dynamic";
import { useRef } from "react";
import { Card, Empty, ErrorNote } from "@/components/ui";
import type { EditorHandle } from "@/components/yaml-editor";
import { useDraft } from "@/components/workflows/draft";

// The editor (CodeMirror) is loaded only when the tab opens: the page does not pay for its weight before.
const YamlEditor = dynamic(() => import("@/components/yaml-editor").then((m) => m.YamlEditor), {
  ssr: false,
  loading: () => <p className="text-sm text-ink-muted">Loading the editor…</p>,
});

/**
 * The workflow's YAML, a VIEW of the shared draft (S21-06): what is typed here is what the map
 * and the process view show, and the draft bar publishes it, with a single button for the three
 * views. Validation follows the typing; it is the server's, the orchestrator's code.
 */
export default function YamlPage() {
  const draft = useDraft();
  const editor = useRef<EditorHandle>(null);
  const { definition, validation } = draft;
  if (definition.error) return <ErrorNote>{String(definition.error)}</ErrorNote>;
  if (!definition.data) return <Empty>Reading the workflow…</Empty>;
  const name = definition.data.name;
  return (
    <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
      <Card title={`${name} — YAML`}>
        <YamlEditor
          ref={editor}
          label={`YAML of ${name}`}
          value={draft.yaml}
          onChange={draft.write}
          readOnly={draft.busy}
          issues={draft.errors}
          warnings={draft.warnings}
        />
        <p className="mt-2 text-xs text-ink-muted">
          Changes typed here, on the map or in the process view are one draft: the bar above publishes it.
        </p>
      </Card>
      <Card title="Validation" className="self-start">
        <div data-testid="validation-yaml" className="space-y-2 text-sm">
          {validation.state === "running" ? (
            <p className="text-ink-muted">Validating…</p>
          ) : validation.state === "unreachable" ? (
            <ErrorNote>
              Could not validate: {validation.message}.{" "}
              <button type="button" className="underline" onClick={draft.revalidate}>
                Retry
              </button>
            </ErrorNote>
          ) : draft.errors.length === 0 ? (
            <p className="text-ok">✓ valid workflow</p>
          ) : null}
          {draft.errors.map((issue, index) => (
            <ErrorNote key={index}>
              {issue.line ? (
                <button type="button" className="underline" onClick={() => editor.current?.goToLine(issue.line!)}>
                  Line {issue.line}
                </button>
              ) : null}
              {issue.line ? " — " : ""}
              {issue.message}
              {issue.path ? ` (${issue.path})` : ""}
            </ErrorNote>
          ))}
          {draft.warnings.map((issue, index) => (
            <p key={index} className="rounded border border-line border-l-2 border-l-warn bg-surface px-3 py-2 text-warn">
              {issue.message}
            </p>
          ))}
        </div>
      </Card>
    </div>
  );
}
