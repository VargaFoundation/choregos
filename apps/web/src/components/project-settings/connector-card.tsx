// SPDX-License-Identifier: Apache-2.0
"use client";

import { useState } from "react";
import { ConnectorIcon } from "@/components/connector-icon";
import { ConnectorForm, type ConnectorBody } from "@/components/project-settings/connector-form";
import type { SecretDraft } from "@/components/secret-field";
import { Badge, Button, Drawer } from "@/components/ui";
import { connectorState } from "@/lib/connector-state";
import { titreDeSorte } from "@/lib/sortes-de-connecteurs";
import type { ConnectorDto, ConnectorType, ProjectRequirement } from "@/lib/types";

/** The config keys that say WHERE a connector points, in the order a person would look for them. */
const CLES_DE_CONFIG = ["repo", "owner", "gitops_repo", "project_key", "base_url", "url", "channel", "namespace"];

/** A one-line reading of the configuration: `owner/repo`, a GitOps repository, a base URL. */
export function configurationEnClair(config: Record<string, unknown> | undefined): string {
  for (const cle of CLES_DE_CONFIG) {
    const valeur = config?.[cle];
    if (typeof valeur === "string" && valeur) return valeur;
  }
  return "";
}

/**
 * Which workflows need a capability. A reason reads `<workflow>: <why>`; the card names the workflows
 * and keeps the full sentences behind a disclosure.
 */
export function workflowsQuiExigent(reasons: string[]): string[] {
  const noms = reasons.map((raison) => /^([\w.-]+): /.exec(raison)?.[1]).filter((nom): nom is string => !!nom);
  return [...new Set(noms)];
}

export function ConnectorCard({
  sorte,
  exigence,
  actuel,
  types,
  onSave,
  onSecret,
  onClearSecret,
  onTest,
}: {
  sorte: string;
  exigence?: ProjectRequirement;
  actuel?: ConnectorDto;
  types: ConnectorType[];
  onSave: (corps: ConnectorBody) => Promise<void>;
  onSecret: (field: string, draft: SecretDraft) => Promise<void>;
  onClearSecret: (field: string) => Promise<void>;
  onTest: () => Promise<void>;
}) {
  const [ouvert, setOuvert] = useState(false);
  const etat = connectorState({ connector: actuel, requirement: exigence, required: exigence !== undefined });
  const typeAffiche = actuel?.type ?? exigence?.default_type ?? undefined;
  const nomDuType = typeAffiche ? (types.find((t) => t.type === typeAffiche)?.display ?? typeAffiche) : undefined;
  const reglage = configurationEnClair(actuel?.config);
  const raisons = exigence?.reasons ?? [];
  const workflows = workflowsQuiExigent(raisons);
  const titre = titreDeSorte(sorte);
  return (
    <li
      className="flex flex-col gap-3 rounded-md border border-line bg-surface p-4"
      data-testid={`connecteur-${sorte}`}
    >
      <div className="flex items-start gap-3">
        <span
          role="img"
          aria-label={nomDuType ?? titre}
          className="grid size-10 shrink-0 place-items-center rounded-md border border-line bg-surface-sunken text-ink"
        >
          <ConnectorIcon kind={sorte} type={typeAffiche ?? ""} size={22} />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-ink">
            {titre} <code className="ml-1 text-xs font-normal text-ink-muted">{sorte}</code>
          </h3>
          {nomDuType && <p className="text-xs break-words text-ink-muted" data-donnee>{nomDuType}</p>}
          {reglage && <p className="font-mono text-xs break-all text-ink-muted">{reglage}</p>}
        </div>
      </div>
      {etat && (
        <div>
          <Badge tone={etat.tone} data-testid={`etat-${sorte}`}>
            {etat.label}
          </Badge>
        </div>
      )}
      {etat && (etat.standIn || (etat.detail && etat.action !== "connect")) && (
        <p className="flex flex-wrap items-center gap-2 text-xs text-ink-muted">
          {etat.standIn && (
            <Badge tone="warn" dot={false}>
              Demo stand-in
            </Badge>
          )}
          {etat.detail && etat.action !== "connect" && etat.label !== "Provided by the platform" && <span>{etat.detail}</span>}
        </p>
      )}
      {workflows.length > 0 && (
        <p className="text-xs text-ink-muted">
          Needed by: <span className="text-ink">{workflows.join(", ")}</span>
        </p>
      )}
      {raisons.length > 0 && (
        <details className="text-xs text-ink-muted">
          <summary className="cursor-pointer">Why it is needed</summary>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">
            {raisons.map((raison) => (
              <li key={raison}>{raison}</li>
            ))}
          </ul>
        </details>
      )}
      <div className="mt-auto flex flex-wrap justify-end gap-2">
        {actuel && (
          <Button size="sm" onClick={() => void onTest()}>
            Test
          </Button>
        )}
        <Button size="sm" onClick={() => setOuvert(true)}>
          {actuel ? "Edit" : "Configure"}
        </Button>
      </div>
      <Drawer open={ouvert} onClose={() => setOuvert(false)} title={`${titre}${nomDuType ? ` · ${nomDuType}` : ""}`}>
        <ConnectorForm
          types={types}
          actuel={actuel}
          onSecret={onSecret}
          onClearSecret={onClearSecret}
          onSave={async (corps) => {
            await onSave(corps);
            setOuvert(false);
          }}
        />
      </Drawer>
    </li>
  );
}
