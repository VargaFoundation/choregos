// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button, Card, Code, ErrorNote, Select } from "@/components/ui";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { Org } from "@/lib/types";

/** The organisation's choice; `null` follows the deployment's default. */
export type OrgToolScripts = NonNullable<Org["tool_scripts"]> | null;

/** The select's value for each choice: the empty string is the deployment's default. */
const OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "Deployment default" },
  { value: "enabled", label: "Enabled" },
  { value: "disabled", label: "Disabled" },
];

/** What each choice means for the organisation's runs. */
export function toolScriptsMeaning(choice: OrgToolScripts): string {
  if (choice === "enabled") {
    return "Each project whose policy offers them (tools.scripts: offer) gets them, unless the policy ceiling forbids them.";
  }
  if (choice === "disabled") return "No run of this organisation is offered them, whatever a project's policy says.";
  return "The organisation follows the deployment's default, which its operator sets (off unless turned on); this page does not see it.";
}

/**
 * Whether the organisation's agents may call their run's tools from a script (S57-05b, ADR 0122 §6):
 * Enabled, Disabled, or the deployment's default. Its administrator saves it with `PATCH /orgs/{org}`
 * (`tool_scripts`); anyone else reads it. The deployment's operator keeps a brake this page does not see.
 */
export function OrgToolScriptsCard({ org, canEdit }: { org: string; canEdit: boolean }) {
  const { orgs } = useSession();
  const client = useQueryClient();
  const stored: OrgToolScripts = (orgs ?? []).find((o) => o.slug === org)?.tool_scripts ?? null;
  const [picked, setPicked] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const shown = picked ?? stored ?? "";
  const choice: OrgToolScripts = shown === "enabled" || shown === "disabled" ? shown : null;

  async function save() {
    setError(null);
    setSaved(false);
    try {
      await api.updateOrg(org, { tool_scripts: choice });
      await client.invalidateQueries({ queryKey: ["orgs"] });
      setPicked(null);
      setSaved(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "refused");
    }
  }

  return (
    <Card title="Tool scripts">
      <form
        className="space-y-3 text-sm"
        data-testid="org-tool-scripts"
        onSubmit={(event) => {
          event.preventDefault();
          if (canEdit) void save();
        }}
      >
        <p className="text-ink-muted">
          Whether this organisation&apos;s agents may also call their run&apos;s tools from a script they write, through the
          runner&apos;s tool bridge, and read only what the script prints. Each call is governed as the model&apos;s own.
        </p>
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1">
            <span className="text-xs text-ink-muted">Tool scripts</span>
            <Select
              value={shown}
              disabled={!canEdit}
              onChange={(event) => {
                setPicked(event.target.value);
                setSaved(false);
              }}
            >
              {OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          </label>
          {canEdit && (
            <Button size="sm" tone="primary" type="submit" disabled={choice === stored}>
              Save tool scripts
            </Button>
          )}
        </div>
        <p className="text-xs text-ink-muted" data-testid="org-tool-scripts-meaning">
          {toolScriptsMeaning(choice)}
        </p>
        <p className="text-xs text-ink-muted">
          The deployment&apos;s operator can still refuse them to every organisation (<Code>runner.toolScripts.allowed: false</Code>).
          {!canEdit && " Only an organisation administrator changes this setting."}
        </p>
        {saved && <p className="text-xs text-ink-muted">Tool scripts saved</p>}
        {error && <ErrorNote>{error}</ErrorNote>}
      </form>
    </Card>
  );
}
