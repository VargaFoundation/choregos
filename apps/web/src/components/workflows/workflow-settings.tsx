// SPDX-License-Identifier: Apache-2.0
"use client";

import { useState } from "react";
import { Button, Card, Input, Select } from "@/components/ui";
import { useBrouillon } from "@/components/workflows/brouillon";
import type { WorkflowDocument } from "@/components/workflows/document";
import { FieldsEditor } from "@/components/workflows/fields-editor";
import { rowsToSchema, schemaToRows } from "@/components/workflows/fields-schema";
import { operations } from "@/components/workflows/operations";
import type { WorkflowOperation } from "@/lib/types";

/** The workflow's own fields (ADR 0112 §3): what it is for, where a request starts, what it carries. */
export function WorkflowSettingsPanel() {
  const draft = useBrouillon();
  const document = draft.document;
  if (!document) return null;
  return <WorkflowSettingsForm key={JSON.stringify([document.metadata, document.initial])} document={document} />;
}

function WorkflowSettingsForm({ document }: { document: WorkflowDocument }) {
  const draft = useBrouillon();
  const [description, setDescription] = useState(document.metadata.description ?? "");
  const [initial, setInitial] = useState(document.initial ?? "");
  const [rows, setRows] = useState(schemaToRows(document.metadata.inputs));
  const states = Object.keys(document.states ?? {});

  function save() {
    const ops: WorkflowOperation[] = [];
    if (description !== (document.metadata.description ?? "") && (description || document.metadata.description))
      ops.push(operations.workflowField("description", description));
    if (initial !== (document.initial ?? "") && (initial || document.initial)) ops.push(operations.workflowField("initial", initial));
    const inputs = rowsToSchema(rows, document.metadata.inputs);
    if (JSON.stringify(inputs) !== JSON.stringify(document.metadata.inputs ?? null) && (inputs || document.metadata.inputs))
      ops.push(operations.workflowField("inputs", inputs));
    if (ops.length > 0) void draft.appliquer(...ops);
  }

  return (
    <Card title="Workflow">
      <form
        className="space-y-3 text-sm"
        data-testid="workflow-settings"
        onSubmit={(event) => {
          event.preventDefault();
          save();
        }}
      >
        <label className="block space-y-1">
          <span className="text-xs text-ink-muted">Description</span>
          <Input value={description} onChange={(e) => setDescription(e.target.value)} />
        </label>
        <label className="block space-y-1">
          <span className="text-xs text-ink-muted">A request starts in</span>
          <Select value={initial} onChange={(e) => setInitial(e.target.value)}>
            <option value="">The first state (default)</option>
            {states.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </Select>
        </label>
        <FieldsEditor label="Fields of a request" rows={rows} onChange={setRows} />
        <Button size="sm" type="submit" disabled={draft.occupe}>
          Save the workflow&apos;s fields
        </Button>
      </form>
    </Card>
  );
}
