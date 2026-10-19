// SPDX-License-Identifier: Apache-2.0
"use client";

import { useState } from "react";
import { ProcedurePanel } from "@/components/procedures/procedure-panel";
import { SchemaForm, champsManquants, type JsonSchema } from "@/components/schema-form";
import {
  EMPTY_DRAFT,
  SecretField,
  bodyOfDrafts,
  problemOfDraft,
  type SecretDraft,
} from "@/components/secret-field";
import { Button, Select } from "@/components/ui";
import { connectorTarget, presetOfMcpUrl } from "@/lib/procedures";
import type { ConnectorDto, ConnectorType } from "@/lib/types";

export type ConnectorBody = {
  type: string;
  config: Record<string, unknown>;
  secret_refs: Record<string, string>;
  secret_values: Record<string, string>;
};

/**
 * The type, its configuration drawn from its schema, and its secrets (ADR 0058). A secret that is
 * already set is replaced or cleared on the spot (its own call, the value never comes back); one
 * that is not is typed here and goes with Save. The references already held are sent again, since a
 * save replaces them; a stored value the body does not name is kept by the API.
 */
export function ConnectorForm({
  types,
  actuel,
  onSave,
  onSecret,
  onClearSecret,
}: {
  types: ConnectorType[];
  actuel?: ConnectorDto;
  onSave: (corps: ConnectorBody) => Promise<void>;
  onSecret: (field: string, draft: SecretDraft) => Promise<void>;
  onClearSecret: (field: string) => Promise<void>;
}) {
  const [type, setType] = useState(actuel?.type ?? types[0]?.type ?? "");
  const [config, setConfig] = useState<Record<string, unknown>>(actuel?.config ?? {});
  const [secrets, setSecrets] = useState<Record<string, SecretDraft>>({});
  const choisi = types.find((t) => t.type === type);
  const schema = (choisi?.config_schema ?? { type: "object", properties: {} }) as JsonSchema;
  const manquants = champsManquants(schema, config);
  const illisibles = Object.values(secrets).filter((draft) => problemOfDraft(draft) !== null);
  const memeType = actuel?.type === type;
  const etats = memeType ? (actuel?.secrets ?? []) : [];
  return (
    <form
      className="mt-3 space-y-3 border-t border-line pt-3 text-sm"
      onSubmit={(event) => {
        event.preventDefault();
        const kept = Object.fromEntries(
          etats.filter((s) => s.source === "reference" && s.reference).map((s) => [s.field, s.reference as string]),
        );
        const { secret_values, secret_refs } = bodyOfDrafts(secrets);
        void onSave({ type, config, secret_refs: { ...kept, ...secret_refs }, secret_values });
      }}
    >
      <label className="block space-y-1">
        <span className="text-xs text-ink-muted">Implementation</span>
        <Select
          className="w-full"
          value={type}
          onChange={(event) => {
            setType(event.target.value);
            setConfig({});
            setSecrets({});
          }}
        >
          {types.map((t) => (
            <option key={t.type} value={t.type}>
              {t.display}
            </option>
          ))}
        </Select>
      </label>
      {choisi && (
        // The form lives in the connector card's drawer (S40-06c): the procedure (S39-23) opens in the
        // drawer's own flow, as the MCP catalogue's install does, not as a side panel over it.
        <div className="flex flex-wrap items-center gap-2" data-testid="connector-procedure">
          <ProcedurePanel
            target={connectorTarget(choisi.kind, choisi.type)}
            preset={choisi.kind === "mcp" ? presetOfMcpUrl(config.url) : undefined}
            autoOpen={!actuel}
            inline
          />
        </div>
      )}
      <SchemaForm schema={schema} value={config} onChange={setConfig} />
      {(choisi?.secret_fields ?? []).length > 0 && (
        <fieldset className="space-y-2">
          <legend className="text-xs text-ink-muted">Secrets — typed once, never shown again</legend>
          {(choisi?.secret_fields ?? []).map((champ) => {
            const etat = etats.find((s) => s.field === champ);
            return etat ? (
              <SecretField
                key={champ}
                field={champ}
                state={etat}
                onSave={(draft) => onSecret(champ, draft)}
                onClear={() => onClearSecret(champ)}
              />
            ) : (
              <SecretField
                key={champ}
                field={champ}
                draft={secrets[champ] ?? EMPTY_DRAFT}
                onChange={(draft) => setSecrets({ ...secrets, [champ]: draft })}
              />
            );
          })}
        </fieldset>
      )}
      <div className="flex justify-end">
        <Button size="sm" tone="primary" type="submit" disabled={!type || manquants.length > 0 || illisibles.length > 0}>
          Save
        </Button>
      </div>
    </form>
  );
}
