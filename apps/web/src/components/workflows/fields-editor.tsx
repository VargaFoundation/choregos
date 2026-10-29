// SPDX-License-Identifier: Apache-2.0
"use client";

import { Button, Input, Select } from "@/components/ui";
import { FIELD_NAME, type FieldRow, type FieldType } from "@/components/workflows/fields-schema";

const TYPES: { value: FieldType; label: string }[] = [
  { value: "string", label: "Text" },
  { value: "number", label: "Number" },
  { value: "integer", label: "Whole number" },
  { value: "boolean", label: "Yes / no" },
  { value: "date", label: "Date" },
];

/** Rows of fields — name, label, type, required — with a field this editor cannot describe kept as written. */
export function FieldsEditor({ rows, onChange, label }: { rows: FieldRow[]; onChange: (rows: FieldRow[]) => void; label: string }) {
  const update = (i: number, change: Partial<FieldRow>) => onChange(rows.map((r, j) => (j === i ? { ...r, ...change } : r)));
  return (
    <fieldset className="space-y-2">
      <legend className="text-xs text-ink-muted">{label}</legend>
      {rows.map((row, i) => (
        <div key={i} className="grid grid-cols-[1fr_1fr_auto_auto_auto] items-end gap-2" data-testid="field-row">
          <Input
            aria-label={`Field ${i + 1} name`}
            value={row.name}
            onChange={(e) => update(i, { name: e.target.value })}
            readOnly={!!row.kept}
          />
          <Input aria-label={`Field ${i + 1} label`} value={row.title} onChange={(e) => update(i, { title: e.target.value })} disabled={!!row.kept} />
          {row.kept ? (
            <span className="text-xs text-ink-muted">kept as written</span>
          ) : (
            <Select aria-label={`Field ${i + 1} type`} value={row.type} onChange={(e) => update(i, { type: e.target.value as FieldType })}>
              {TYPES.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </Select>
          )}
          <label className="flex items-center gap-1 text-xs">
            <input type="checkbox" checked={row.required} onChange={(e) => update(i, { required: e.target.checked })} />
            required
          </label>
          <button
            type="button"
            className="text-xs text-ink-muted underline hover:text-danger"
            aria-label={`Remove field ${row.name || i + 1}`}
            onClick={() => onChange(rows.filter((_, j) => j !== i))}
          >
            Remove
          </button>
          {row.name && !FIELD_NAME.test(row.name) && (
            <p className="col-span-5 text-xs text-warn">Lowercase letters, digits and `_`, starting with a letter.</p>
          )}
        </div>
      ))}
      <Button size="sm" onClick={() => onChange([...rows, { name: "", title: "", type: "string", required: false }])}>
        Add a field
      </Button>
    </fieldset>
  );
}
