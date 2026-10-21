// SPDX-License-Identifier: Apache-2.0
"use client";

import { useState, type ReactNode } from "react";
import { STAGE_ROLES } from "@/components/model-catalogue";
import { Button, ErrorNote, Input, Table, TBody, TD, TH, THead, TR } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import {
  BUDGET_ROLES,
  PLATFORM_DEFAULTS,
  SIZES,
  fieldKey,
  mapServerErrors,
  mergeFormInto,
  toFormValues,
  validate,
} from "@/lib/policy-ceiling-form";

type Values = Record<string, string>;

type CellProps = {
  name: string;
  label: string;
  unit?: string;
  placeholder?: string;
  value: string;
  error?: string;
  readOnly: boolean;
  onChange: (name: string, text: string) => void;
};

/** One number: empty means "no cap"; the placeholder shows what the platform applies without a cap. */
function Cell({ name, label, unit, placeholder, value, error, readOnly, onChange }: CellProps) {
  const messageId = `${name}-error`;
  return (
    <div>
      <div className="flex items-center gap-1.5">
        <Input
          aria-label={label}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? messageId : undefined}
          inputMode="decimal"
          className="w-24"
          value={value}
          readOnly={readOnly}
          placeholder={placeholder ?? "no cap"}
          onChange={(e) => onChange(name, e.target.value)}
        />
        {unit && <span className="text-xs text-ink-muted">{unit}</span>}
      </div>
      {error && (
        <p id={messageId} className="mt-1 text-xs text-danger">
          {error}
        </p>
      )}
    </div>
  );
}

function Section({ title, children, explain }: { title: string; explain: string; children: ReactNode }) {
  return (
    <section className="space-y-2 border-t border-line pt-3" aria-label={title}>
      <h3 className="text-sm font-medium">{title}</h3>
      <p className="text-xs text-ink-muted">{explain}</p>
      {children}
    </section>
  );
}

const ROLE_LABEL = (role: string) => (role === "default" ? "default (every other role)" : role);

/**
 * The structured form of the policy ceiling (S42-08). Every field is a cap: empty means no cap and
 * leaves the key out of what is saved. Saving reads the ceiling again and replaces only what the form
 * owns, so the network card, the raw JSON and another administrator are never erased.
 */
export function PolicyCeilingForm({
  org,
  stored,
  canEdit,
  onSaved,
}: {
  org: string;
  stored: Record<string, unknown> | null | undefined;
  canEdit: boolean;
  onSaved: () => Promise<void>;
}) {
  const [edits, setEdits] = useState<Values>({});
  const [serverFields, setServerFields] = useState<Values>({});
  const [general, setGeneral] = useState<string[]>([]);
  const [tried, setTried] = useState(false);
  const [saved, setSaved] = useState(false);
  const values: Values = { ...toFormValues(stored), ...edits };
  const local = validate(values);
  const errors: Values = { ...(tried ? local : {}), ...serverFields };
  const readOnly = !canEdit;

  function change(name: string, text: string) {
    setEdits((current) => ({ ...current, [name]: text }));
    setServerFields(({ [name]: _gone, ...rest }) => rest);
    setSaved(false);
  }

  const cell = (path: string[], label: string, unit?: string, placeholder?: string) => {
    const name = fieldKey(path);
    return <Cell name={name} label={label} unit={unit} placeholder={placeholder} value={values[name] ?? ""} error={errors[name]} readOnly={readOnly} onChange={change} />;
  };
  const num = (n: number) => `platform: ${n}`;

  async function save() {
    setTried(true);
    setSaved(false);
    setGeneral([]);
    setServerFields({});
    if (Object.keys(local).length) return;
    try {
      // Read the ceiling again: the network card, the raw JSON or another administrator may have changed the rest.
      const fresh = await api.policyCeiling(org);
      const merged = mergeFormInto(fresh.ceiling, values);
      await api.putPolicyCeiling(org, Object.keys(merged).length ? merged : null);
      setEdits({});
      setTried(false);
      setSaved(true);
      await onSaved();
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 422) {
        const mapped = mapServerErrors(cause.problem.errors);
        setServerFields(mapped.fields);
        setGeneral(mapped.general.length || Object.keys(mapped.fields).length ? mapped.general : [cause.message]);
      } else setGeneral([cause instanceof Error ? cause.message : "refused"]);
    }
  }

  return (
    <div className="space-y-4" data-testid="policy-ceiling-form">
      <p className="text-ink-muted">
        Caps every project policy of {org} stays under: a project may choose anything below a cap, nothing above. Leave a field empty for
        no cap; the grey figure is what the platform applies when nobody sets one.
      </p>

      <Section title="Budgets" explain="Money, turns and time an agent may spend. Per ticket size: S, M, L, XL.">
        <p className="text-xs font-medium">Per ticket</p>
        <div className="flex flex-wrap gap-3">
          {SIZES.map((size) => (
            <div key={size} className="space-y-1">
              <span className="text-xs text-ink-muted">Size {size}</span>
              {cell(["budgets", "ticket_usd", size], `Ticket budget ${size}`, "$", num(PLATFORM_DEFAULTS.ticket_usd[size]))}
            </div>
          ))}
        </div>
        <details className="rounded border border-line px-3 py-2">
          <summary className="cursor-pointer text-xs font-medium">Per-stage budgets</summary>
          <p className="my-2 text-xs text-ink-muted">What one stage (one agent run) may cost, by role and ticket size, in dollars.</p>
          <Table>
            <THead>
              <TR>
                <TH>Role</TH>
                {SIZES.map((size) => (
                  <TH key={size}>{size}</TH>
                ))}
              </TR>
            </THead>
            <TBody>
              {BUDGET_ROLES.map((role) => (
                <TR key={role}>
                  <TD>{ROLE_LABEL(role)}</TD>
                  {SIZES.map((size) => (
                    <TD key={size}>{cell(["budgets", "stage_usd", role, size], `Stage budget ${role} ${size}`, "$", num(PLATFORM_DEFAULTS.stage_usd[size]))}</TD>
                  ))}
                </TR>
              ))}
            </TBody>
          </Table>
        </details>
        <p className="text-xs font-medium">Per role</p>
        <Table>
          <THead>
            <TR>
              <TH>Role</TH>
              <TH>Max turns</TH>
              <TH>Max minutes</TH>
            </TR>
          </THead>
          <TBody>
            {BUDGET_ROLES.map((role) => (
              <TR key={role}>
                <TD>{ROLE_LABEL(role)}</TD>
                <TD>{cell(["budgets", "max_turns", role], `Max turns ${role}`, "turns", num(PLATFORM_DEFAULTS.max_turns))}</TD>
                <TD>{cell(["budgets", "max_minutes", role], `Max minutes ${role}`, "min", num(PLATFORM_DEFAULTS.max_minutes))}</TD>
              </TR>
            ))}
          </TBody>
        </Table>
        <div className="grid gap-3 sm:grid-cols-3">
          <label className="space-y-1 text-xs">
            <span className="font-medium">Tool calls per run</span>
            {cell(["budgets", "tool_calls_per_run"], "Tool calls per run", "calls")}
          </label>
          <label className="space-y-1 text-xs">
            <span className="font-medium">Daily spend per project</span>
            {cell(["budgets", "daily_project_usd"], "Daily spend per project", "$ / day")}
          </label>
          <label className="space-y-1 text-xs">
            <span className="font-medium">Monthly spend of the organisation</span>
            {cell(["budgets", "monthly_org_usd"], "Monthly spend of the organisation", "$ / month")}
          </label>
        </div>
      </Section>

      <Section title="Attempts" explain="How many times a stage is retried, and how many times the definition of done may send work back.">
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="space-y-1 text-xs">
            <span className="font-medium">Default attempts (accepted, not enforced)</span>
            {cell(["attempts", "default_max"], "Default attempts", "tries", num(PLATFORM_DEFAULTS.attempts_default_max))}
          </label>
          <label className="space-y-1 text-xs">
            <span className="font-medium">Definition-of-done iterations</span>
            {cell(["attempts", "dod_iterations"], "Definition-of-done iterations", "rounds", num(PLATFORM_DEFAULTS.dod_iterations))}
          </label>
        </div>
        <details className="rounded border border-line px-3 py-2">
          <summary className="cursor-pointer text-xs font-medium">Attempts per role</summary>
          <div className="mt-2 grid gap-3 sm:grid-cols-3">
            {STAGE_ROLES.map((role) => (
              <label key={role} className="space-y-1 text-xs">
                <span className="font-medium">{role}</span>
                {cell(["attempts", "by_role", role], `Attempts ${role}`, "tries")}
              </label>
            ))}
          </div>
        </details>
      </Section>

      <Section title="Scope" explain="How much of a repository an agent may touch.">
        <div className="grid gap-3 sm:grid-cols-3">
          <label className="space-y-1 text-xs">
            <span className="font-medium">Files granted without asking</span>
            {cell(["scope", "auto_grant_max_files"], "Files granted without asking", "files", num(PLATFORM_DEFAULTS.auto_grant_max_files))}
          </label>
          <label className="space-y-1 text-xs">
            <span className="font-medium">Files in a diff</span>
            {cell(["scope", "max_diff_files"], "Files in a diff", "files", num(PLATFORM_DEFAULTS.max_diff_files))}
          </label>
          <label className="space-y-1 text-xs">
            <span className="font-medium">Lines in a diff</span>
            {cell(["scope", "max_diff_lines"], "Lines in a diff", "lines", num(PLATFORM_DEFAULTS.max_diff_lines))}
          </label>
        </div>
      </Section>

      <Section title="Findings" explain="How many findings a single run may report.">
        <label className="block space-y-1 text-xs">
          <span className="font-medium">Findings per run</span>
          {cell(["findings", "max_per_run"], "Findings per run", "findings", num(PLATFORM_DEFAULTS.findings_max_per_run))}
        </label>
      </Section>

      {canEdit && (
        <div className="flex items-center gap-3">
          <Button onClick={() => void save()}>Save the ceiling</Button>
          {saved && <span className="text-sm text-ok">Ceiling saved</span>}
        </div>
      )}
      {tried && Object.keys(local).length > 0 && <ErrorNote>Fix the highlighted fields before saving.</ErrorNote>}
      {general.map((message) => (
        <ErrorNote key={message}>{message}</ErrorNote>
      ))}
    </div>
  );
}
