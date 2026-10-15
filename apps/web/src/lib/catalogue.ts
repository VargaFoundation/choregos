// SPDX-License-Identifier: Apache-2.0
/**
 * What the MCP and skills catalogues share on the client side (ADR 0065): a free-text match, the
 * filters of the page, and the way a count of stars is written. Pure functions, no React.
 */

export interface CatalogueFilters {
  /** Free text over what an entry says about itself. */
  q: string;
  /** A category of the catalogue; empty means every one. */
  category: string;
  /** The toggles that are on, by key (`official`, `installable`…). */
  on: string[];
}

export const NO_FILTERS: CatalogueFilters = { q: "", category: "", on: [] };

/** Every word of the search must appear somewhere in the entry's text, in any order. */
export function matchesText(haystack: string, q: string): boolean {
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  const text = haystack.toLowerCase();
  return words.every((word) => text.includes(word));
}

export function toggled(filters: CatalogueFilters, key: string): boolean {
  return filters.on.includes(key);
}

export function withToggle(filters: CatalogueFilters, key: string, on: boolean): CatalogueFilters {
  const rest = filters.on.filter((k) => k !== key);
  return { ...filters, on: on ? [...rest, key] : rest };
}

/** `63 088`, the way the console writes a count. */
export function stars(count: number): string {
  return new Intl.NumberFormat("en-US").format(count).replaceAll(",", " ");
}

/** The categories present in a list, sorted, each once. */
export function categoriesOf(entries: { categories: string[] }[]): string[] {
  return [...new Set(entries.flatMap((e) => e.categories))].sort();
}

/** `developer-tools` → `Developer tools`. */
export function categoryLabel(category: string): string {
  const spaced = category.replaceAll("-", " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}
