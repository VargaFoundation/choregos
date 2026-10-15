// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Confirmation } from "@/components/geste-confirme";
import { Alert, Badge, Button, Card, ErrorNote, EtatDeLecture, Input, Select, Table, TBody, TD, TH, THead, TR } from "@/components/ui";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { ModelCatalogue, ModelCatalogueEntry, ModelCuratePreview, ModelTiers } from "@/lib/types";

/** The stage roles a model can be limited to (empty: every role). */
export const STAGE_ROLES = ["triage", "refine", "plan", "implement", "verify", "review", "fix_ci", "address_review", "release_notes", "verify_prod", "custom"] as const;
export const USAGES = ["agent", "embed", "distill", "eval"] as const;
export const TIERS = ["cheap", "standard", "strong", "aux", "embed", "local"] as const;

/** What a gateway that cannot cap a key means for the budget, said where the gateway is named. */
export const NO_HARD_BUDGET = "Budgets are enforced by Choregos only: this gateway cannot cap a key.";

const when = (iso?: string | null) => (iso ? new Date(iso).toLocaleDateString() : "never");
const price = (entry: ModelCatalogueEntry) =>
  entry.price_override
    ? `${entry.price_override.input_per_1k} / ${entry.price_override.output_per_1k}`
    : entry.gateway?.input_cost_per_1k != null || entry.gateway?.output_cost_per_1k != null
      ? `${entry.gateway?.input_cost_per_1k ?? "?"} / ${entry.gateway?.output_cost_per_1k ?? "?"}`
      : "no price";

export function ModelCataloguePanel() {
  const { org, orgs } = useSession();
  const client = useQueryClient();
  const catalogue = useQuery({ queryKey: ["model-catalogue", org], queryFn: () => api.modelCatalogue(org), retry: false });
  const canEdit = (orgs ?? []).find((o) => o.slug === org)?.role === "org_admin";
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<ModelCuratePreview | null>(null);

  async function act(gesture: () => Promise<unknown>, done?: string) {
    setError(null);
    setMessage(null);
    try {
      await gesture();
      if (done) setMessage(done);
      await client.invalidateQueries({ queryKey: ["model-catalogue", org] });
      await client.invalidateQueries({ queryKey: ["usable-models", org] });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Refused");
    }
  }

  if (catalogue.error || !catalogue.data) {
    return <EtatDeLecture lecture={catalogue} quoi="the model catalogue" />;
  }
  const data = catalogue.data;
  const enabled = data.entries.filter((e) => e.enabled);
  const hardBudget = (data.gateway.capabilities ?? []).includes("hard_budget");

  async function setMode(mode: "open" | "curated") {
    await act(() => api.putModelCatalogueSettings(org, { mode, tiers: tiersBody(data.tiers) }), `Mode set to ${mode}`);
  }

  return (
    <div className="space-y-6">
      <Card title="Gateway">
        <div className="space-y-2 text-sm" data-testid="gateway">
          <p>
            Models are routed by the <span className="font-mono">{data.gateway.type}</span> gateway.
          </p>
          <p className="flex flex-wrap gap-2">
            {(data.gateway.capabilities ?? []).length === 0 ? (
              <span className="text-ink-muted">This gateway declares no capability.</span>
            ) : (
              (data.gateway.capabilities ?? []).map((c) => (
                <Badge key={c} tone="neutral">
                  {c}
                </Badge>
              ))
            )}
          </p>
          {!hardBudget && <Alert tone="warn">{NO_HARD_BUDGET}</Alert>}
        </div>
      </Card>

      {message && <p role="status" className="text-sm text-ink-muted">{message}</p>}
      {error && <ErrorNote>{error}</ErrorNote>}

      <Card title="Mode">
        <div className="space-y-3 text-sm">
          <p>
            <strong>{data.mode === "open" ? "Open" : "Curated"}.</strong>{" "}
            {data.mode === "open"
              ? "Every model the gateway lists is usable, except the ones disabled below."
              : "Only the models enabled below are usable; a model discovered later arrives disabled."}
          </p>
          {canEdit && data.mode === "open" && !preview && (
            <Button
              onClick={() => void act(async () => setPreview(await api.previewModelCuration(org)))}
              title="Shows what would be refused before anything changes"
            >
              Switch to curated…
            </Button>
          )}
          {canEdit && data.mode === "open" && preview && (
            <Confirmation
              ton="primary"
              confirmer="Switch to curated"
              question={
                <span data-testid="curation-preview">
                  {preview.refused?.length
                    ? `Switching to curated would refuse ${preview.refused.length} use${preview.refused.length > 1 ? "s" : ""} of a model that is not enabled: ${preview.refused.map((r) => `${r.kind === "agent" ? "agent" : "project profile"} ${r.ref} (${r.model})`).join(", ")}.`
                    : "Nothing in use would be refused."}
                </span>
              }
              action={() => api.putModelCatalogueSettings(org, { mode: "curated", tiers: tiersBody(data.tiers) })}
              onFait={() => {
                setPreview(null);
                setMessage("Mode set to curated");
                void client.invalidateQueries({ queryKey: ["model-catalogue", org] });
              }}
              onAnnuler={() => setPreview(null)}
            />
          )}
          {canEdit && data.mode === "curated" && <Button onClick={() => void setMode("open")}>Switch back to open</Button>}
        </div>
      </Card>

      <Card title="Tiers">
        <div className="space-y-2 text-sm">
          <p className="text-ink-muted">
            The model behind each tier for this organisation. A project&apos;s own mapping wins; empty keeps the platform&apos;s
            alias.
          </p>
          <div className="grid gap-2 sm:grid-cols-2">
            {TIERS.map((tier) => (
              <label key={tier} className="flex items-center justify-between gap-3">
                <span className="font-mono text-xs">{tier}</span>
                <Select
                  aria-label={`Model of the ${tier} tier`}
                  value={data.tiers[tier] ?? ""}
                  disabled={!canEdit}
                  onChange={(e) =>
                    void act(
                      () => api.putModelCatalogueSettings(org, { mode: data.mode, tiers: { ...tiersBody(data.tiers), [tier]: e.target.value || null } }),
                      `Tier ${tier} saved`,
                    )
                  }
                  className="w-64 min-w-0"
                >
                  <option value="">The platform&apos;s alias</option>
                  {enabled.map((e) => (
                    <option key={e.id} value={e.model_name}>
                      {e.display_name || e.model_name}
                    </option>
                  ))}
                </Select>
              </label>
            ))}
          </div>
        </div>
      </Card>

      <Card
        title="Models"
        action={
          canEdit && (
            <Button
              tone="accent"
              onClick={() => void act(async () => {
                const r = await api.syncModelCatalogue(org);
                setMessage(`Synced: ${r.added} added, ${r.missing} no longer listed, ${r.seen} seen`);
              })}
            >
              Sync from gateway
            </Button>
          )
        }
      >
        <div className="space-y-4">
          {data.entries.length === 0 ? (
            <p className="text-sm text-ink-muted" data-testid="no-models">
              No model in the catalogue yet. {canEdit ? "Sync from the gateway, or add one by hand." : "An administrator syncs it from the gateway."}
            </p>
          ) : (
            <div className="overflow-x-auto">
              <Table data-testid="model-entries">
                <THead>
                  <TR>
                    <TH>Enabled</TH>
                    <TH>Model</TH>
                    <TH>Price per 1k (in / out)</TH>
                    <TH>Roles</TH>
                    <TH>Usages</TH>
                    <TH>Seen</TH>
                    <TH>
                      <span className="sr-only">Actions</span>
                    </TH>
                  </TR>
                </THead>
                <TBody>
                  {data.entries.map((entry) => (
                    <EntryRow key={entry.id} entry={entry} canEdit={canEdit} org={org} act={act} />
                  ))}
                </TBody>
              </Table>
            </div>
          )}
          {canEdit && <AddModel org={org} act={act} />}
        </div>
      </Card>
    </div>
  );
}

/** The tiers as the API takes them back: every key present. */
function tiersBody(tiers: ModelTiers): ModelTiers {
  return Object.fromEntries(TIERS.map((t) => [t, tiers[t] ?? null])) as ModelTiers;
}

function EntryRow({
  entry,
  canEdit,
  org,
  act,
}: {
  entry: ModelCatalogue["entries"][number];
  canEdit: boolean;
  org: string;
  act: (gesture: () => Promise<unknown>, done?: string) => Promise<void>;
}) {
  const [input, setInput] = useState(entry.price_override?.input_per_1k?.toString() ?? "");
  const [output, setOutput] = useState(entry.price_override?.output_per_1k?.toString() ?? "");
  const patch = (body: Parameters<typeof api.updateModelCatalogueEntry>[2], done: string) =>
    act(() => api.updateModelCatalogueEntry(org, entry.id, body), done);
  const roles = entry.allowed_roles ?? [];
  return (
    <TR data-testid={`model-${entry.model_name}`}>
      <TD>
        <input
          type="checkbox"
          aria-label={`Enable ${entry.model_name}`}
          checked={entry.enabled}
          disabled={!canEdit}
          onChange={(e) => void patch({ enabled: e.target.checked }, `${entry.model_name} ${e.target.checked ? "enabled" : "disabled"}`)}
        />
      </TD>
      <TD>
        <span className="font-medium">{entry.display_name || entry.model_name}</span>
        <span className="block font-mono text-xs text-ink-muted">
          {entry.model_name}
          {entry.gateway?.provider ? ` · ${entry.gateway.provider}` : ""}
        </span>
        {entry.source === "manual" && <Badge tone="neutral">added by hand</Badge>}
      </TD>
      <TD>
        <span className="text-xs text-ink-muted">{price(entry)}</span>
        {canEdit && (
          <span className="mt-1 flex items-center gap-1">
            <Input aria-label={`Input price of ${entry.model_name}`} value={input} onChange={(e) => setInput(e.target.value)} placeholder="in" className="w-16 min-w-0" />
            <Input aria-label={`Output price of ${entry.model_name}`} value={output} onChange={(e) => setOutput(e.target.value)} placeholder="out" className="w-16 min-w-0" />
            <Button
              size="sm"
              disabled={input === "" || output === "" || Number.isNaN(Number(input)) || Number.isNaN(Number(output))}
              onClick={() => void patch({ price_override: { input_per_1k: Number(input), output_per_1k: Number(output) } }, "Price saved")}
            >
              Override
            </Button>
            {entry.price_override && (
              <Button size="sm" onClick={() => void patch({ clear_price_override: true }, "Override removed")}>
                Clear
              </Button>
            )}
          </span>
        )}
      </TD>
      <TD>
        <details>
          <summary className="cursor-pointer text-xs">{roles.length ? roles.join(", ") : "all roles"}</summary>
          <div className="mt-1 flex flex-col gap-1">
            {STAGE_ROLES.map((role) => (
              <label key={role} className="flex items-center gap-1 text-xs">
                <input
                  type="checkbox"
                  aria-label={`${role} for ${entry.model_name}`}
                  checked={roles.includes(role)}
                  disabled={!canEdit}
                  onChange={(e) =>
                    void patch({ allowed_roles: e.target.checked ? [...roles, role] : roles.filter((r) => r !== role) }, "Roles saved")
                  }
                />
                {role}
              </label>
            ))}
          </div>
        </details>
      </TD>
      <TD>
        <span className="flex flex-col gap-1">
          {USAGES.map((usage) => (
            <label key={usage} className="flex items-center gap-1 text-xs">
              <input
                type="checkbox"
                aria-label={`${usage} usage of ${entry.model_name}`}
                checked={(entry.usages ?? []).includes(usage)}
                disabled={!canEdit}
                onChange={(e) =>
                  void patch(
                    { usages: e.target.checked ? [...(entry.usages ?? []), usage] : (entry.usages ?? []).filter((u) => u !== usage) },
                    "Usages saved",
                  )
                }
              />
              {usage}
            </label>
          ))}
        </span>
      </TD>
      <TD>
        {entry.missing_since ? (
          <Badge tone="warn" title="The gateway no longer lists it; its settings are kept">
            gone since {when(entry.missing_since)}
          </Badge>
        ) : (
          <span className="text-xs text-ink-muted">{entry.source === "manual" ? "—" : when(entry.last_seen_at)}</span>
        )}
      </TD>
      <TD>
        {canEdit && entry.source === "manual" && (
          <Button size="sm" tone="danger" onClick={() => void act(() => api.deleteModelCatalogueEntry(org, entry.id), `${entry.model_name} removed`)}>
            Remove
          </Button>
        )}
      </TD>
    </TR>
  );
}

function AddModel({ org, act }: { org: string; act: (gesture: () => Promise<unknown>, done?: string) => Promise<void> }) {
  const [name, setName] = useState("");
  return (
    <form
      className="flex flex-wrap items-end gap-2 text-sm"
      aria-label="Add a model manually"
      onSubmit={(event) => {
        event.preventDefault();
        void act(() => api.addModelCatalogueEntry(org, { model_name: name.trim() }), `${name.trim()} added`).then(() => setName(""));
      }}
    >
      <label className="flex min-w-0 flex-col gap-1">
        <span className="text-xs text-ink-muted">A model the gateway routes but does not list</span>
        <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="provider/model-name" className="w-64 min-w-0 font-mono" />
      </label>
      <Button type="submit" disabled={!name.trim()}>
        Add a model manually
      </Button>
    </form>
  );
}
