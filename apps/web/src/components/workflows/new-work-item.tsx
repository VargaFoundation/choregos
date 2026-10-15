// SPDX-License-Identifier: Apache-2.0
"use client";

import { useState } from "react";
import { Button, ErrorNote, Input, Select, Textarea } from "@/components/ui";
import { champsDepuisSchema, valeursPourLApi, type Champ } from "@/components/workflows/champs";
import { useWorkflow } from "@/components/workflows/use-workflow";
import { api } from "@/lib/api";
import type { TrackerInfo } from "@/lib/tracker";
import type { WorkItemDto } from "@/lib/types";

/**
 * File a work item: its workflow, the inputs that workflow declares (`metadata.inputs`), a title, a
 * description, a size and an optional budget. With an external tracker the platform creates the issue
 * there first and records the work item under its key (ADR 0059); the API validates the fields.
 */
export function NewWorkItemForm({
  slug,
  workflows,
  initial,
  tracker,
  onDone,
}: {
  slug: string;
  workflows: string[];
  initial: string;
  tracker: TrackerInfo;
  onDone: (item: WorkItemDto) => void;
}) {
  const [workflow, setWorkflow] = useState(initial);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [size, setSize] = useState<"S" | "M" | "L" | "XL" | "">("");
  const [budget, setBudget] = useState("");
  const [start, setStart] = useState(true);
  const [entered, setEntered] = useState<Record<string, string | boolean>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { definition } = useWorkflow(slug, workflow);
  const fields = champsDepuisSchema(definition.data?.json?.metadata?.inputs as Record<string, unknown> | undefined);

  async function file() {
    setBusy(true);
    setError(null);
    try {
      const created = await api.createWorkItem(slug, {
        title,
        body,
        size: size || null,
        start,
        workflow: workflow || null,
        fields: valeursPourLApi(fields, entered),
        budget_usd: budget ? Number(budget) : null,
      });
      onDone(created);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "request refused");
      setBusy(false);
    }
  }

  return (
    <form
      className="space-y-3 text-sm"
      onSubmit={(event) => {
        event.preventDefault();
        void file();
      }}
    >
      <p className="text-ink-muted" data-testid="tracker-notice">
        {tracker.external
          ? `Creates the issue in ${tracker.type}${tracker.where ? ` (${tracker.where})` : ""} and starts it here.`
          : "Filed in Choregos: the platform gives it its key."}
      </p>
      <label className="block space-y-1">
        <span className="text-ink-muted">Workflow</span>
        <Select
          aria-label="Workflow"
          value={workflow}
          disabled={workflows.length <= 1}
          onChange={(event) => {
            setWorkflow(event.target.value);
            setEntered({});
          }}
          className="w-full"
        >
          {(workflows.length ? workflows : [initial]).map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </Select>
      </label>
      {fields.length > 0 && (
        <fieldset className="grid gap-2" data-testid="request-fields">
          <legend className="sr-only">Fields of the work item</legend>
          {fields.map((field) => (
            <InputField
              key={field.name}
              champ={field}
              valeur={entered[field.name]}
              onChange={(value) => setEntered((before) => ({ ...before, [field.name]: value }))}
            />
          ))}
        </fieldset>
      )}
      <Input
        aria-label="Title"
        value={title}
        onChange={(event) => setTitle(event.target.value)}
        placeholder="Data project manager for a 6-month assignment"
        className="w-full"
      />
      <Textarea
        aria-label="Description"
        value={body}
        onChange={(event) => setBody(event.target.value)}
        rows={5}
        placeholder="Client, start date, expected outcome, constraints…"
        className="w-full"
      />
      <div className="flex flex-wrap items-center gap-2">
        <Select
          aria-label="Size"
          value={size}
          onChange={(event) => setSize(event.target.value as typeof size)}
          className="w-auto"
        >
          <option value="">Size?</option>
          {["S", "M", "L", "XL"].map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </Select>
        <Input
          aria-label="Budget in USD"
          type="number"
          min="0.01"
          step="0.01"
          value={budget}
          onChange={(event) => setBudget(event.target.value)}
          placeholder="max budget, $"
          className="w-36"
        />
      </div>
      <p className="text-xs text-ink-muted">A budget only tightens the workflow&apos;s own.</p>
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={start} onChange={(event) => setStart(event.target.checked)} />
        Start now
      </label>
      <Button tone="primary" size="sm" type="submit" disabled={busy || !title}>
        Create and start
      </Button>
      {error && <ErrorNote>{error}</ErrorNote>}
    </form>
  );
}

/** One input of the work item, as the control its type calls for. */
function InputField({
  champ,
  valeur,
  onChange,
}: {
  champ: Champ;
  valeur: string | boolean | undefined;
  onChange: (valeur: string | boolean) => void;
}) {
  const libelle = `${champ.label}${champ.required ? " (required)" : ""}`;
  if (champ.control === "boolean") {
    return (
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={Boolean(valeur)} onChange={(event) => onChange(event.target.checked)} />
        {libelle}
      </label>
    );
  }
  if (champ.control === "choice") {
    return (
      <Select aria-label={libelle} value={String(valeur ?? "")} onChange={(event) => onChange(event.target.value)}>
        <option value="">{libelle}</option>
        {champ.choices?.map((choix) => (
          <option key={choix} value={choix}>
            {choix}
          </option>
        ))}
      </Select>
    );
  }
  const type =
    champ.control === "date" ? "date" : champ.control === "number" || champ.control === "integer" ? "number" : "text";
  return (
    <Input
      aria-label={libelle}
      title={champ.hint}
      type={type}
      required={champ.required}
      value={String(valeur ?? "")}
      placeholder={champ.control === "list" ? `${champ.label}, comma separated` : champ.label}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}
