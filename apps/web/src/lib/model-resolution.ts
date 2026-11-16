// SPDX-License-Identifier: Apache-2.0
/**
 * What an agent's `model` field resolves to for one organisation, read from the organisation's model
 * catalogue (`GET /orgs/{org}/models`): the TypeScript twin of `choregos_core.models` for the part a
 * page needs, `profile:by_size` included. A pure function, so a test pins it.
 *
 * It only says what the catalogue knows. A model the catalogue does not list, a provider or a
 * reasoning flag the gateway never stated: the field stays `null` and the page says nothing.
 */
import type { ModelCatalogue } from "./types";

export const BY_SIZE = "profile:by_size";

/** `choregos_core.models.BY_SIZE_MAP`: the profile of each work item size, and the factor on its turns. */
export const BY_SIZE_MAP: ReadonlyArray<{ size: string; profile: string; turnsFactor: number }> = [
  { size: "S", profile: "standard", turnsFactor: 1 },
  { size: "M", profile: "standard", turnsFactor: 1 },
  { size: "L", profile: "strong", turnsFactor: 1 },
  { size: "XL", profile: "strong", turnsFactor: 1.5 },
];

export interface ResolvedModel {
  /** `S`, `M`, `L`, `XL` for `profile:by_size`; `null` when the size does not matter. */
  size: string | null;
  /** The profile (`standard`, `strong`…) the request went through; `null` for a direct model name. */
  profile: string | null;
  /** The gateway name (`platform/standard`). */
  modelName: string;
  /** What the organisation calls it; the gateway name when it never renamed it. */
  displayName: string;
  /** The model behind the alias, as the gateway says (`anthropic/claude-sonnet-5`). */
  upstream: string | null;
  /** The provider as the gateway states it (`openai`, `anthropic`…); `null` when it did not. */
  provider: string | null;
  /** `true` / `false` only when the gateway said so. */
  reasoning: boolean | null;
  /** Whether the catalogue lists the model at all. */
  known: boolean;
  turnsFactor: number;
}

function resolveName(catalogue: ModelCatalogue | undefined, name: string, size: string | null, profile: string | null, turnsFactor: number): ResolvedModel {
  const entry = catalogue?.entries.find((e) => e.model_name === name);
  const facts = entry?.gateway ?? null;
  const upstream = facts?.upstream_model?.trim() || null;
  const provider = facts?.provider?.trim() || (upstream?.includes("/") ? upstream.split("/", 1)[0]! : null);
  return {
    size,
    profile,
    modelName: name,
    displayName: entry?.display_name || name,
    upstream,
    provider: provider || null,
    reasoning: typeof facts?.supports_reasoning === "boolean" ? facts.supports_reasoning : null,
    known: entry != null,
    turnsFactor,
  };
}

/** The model each size of the request resolves to, in one row for a profile or a direct model. */
export function resolveModel(requested: string | null | undefined, catalogue: ModelCatalogue | undefined): ResolvedModel[] {
  const model = requested?.trim();
  if (!model) return [];
  const viaTier = (tier: string) => catalogue?.tiers[tier as keyof ModelCatalogue["tiers"]] ?? `platform/${tier}`;
  if (model === BY_SIZE) {
    return BY_SIZE_MAP.map(({ size, profile, turnsFactor }) => resolveName(catalogue, viaTier(profile), size, profile, turnsFactor));
  }
  if (model.startsWith("profile:")) {
    const profile = model.slice("profile:".length);
    return [resolveName(catalogue, viaTier(profile), null, profile, 1)];
  }
  return [resolveName(catalogue, model, null, null, 1)];
}

/** A row per distinct model, with the sizes it serves (`S, M` on the standard model, `L, XL` on the strong one) and the sizes whose turn limit it scales. */
export function groupBySize(rows: ResolvedModel[]): Array<{ row: ResolvedModel; sizes: string[]; scaled: Array<{ size: string; factor: number }> }> {
  const out: Array<{ row: ResolvedModel; sizes: string[]; scaled: Array<{ size: string; factor: number }> }> = [];
  for (const row of rows) {
    let group = out.find((g) => g.row.modelName === row.modelName);
    if (!group) {
      group = { row, sizes: [], scaled: [] };
      out.push(group);
    }
    if (row.size) group.sizes.push(row.size);
    if (row.size && row.turnsFactor !== 1) group.scaled.push({ size: row.size, factor: row.turnsFactor });
  }
  return out;
}
