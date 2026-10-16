// SPDX-License-Identifier: Apache-2.0
"use client";

import { categoryLabel, toggled, withToggle, type CatalogueFilters } from "@/lib/catalogue";
import { Input, Select } from "@/components/ui";

/**
 * The search and the facets of a catalogue (ADR 0065), the same for MCP servers and for skills:
 * a free-text box, a category, and the toggles the page offers. Every control has a visible label.
 */
export function CatalogueFacets({
  filters,
  onChange,
  categories,
  toggles,
  shown,
  total,
  noun,
}: {
  filters: CatalogueFilters;
  onChange: (next: CatalogueFilters) => void;
  categories: string[];
  toggles: { key: string; label: string }[];
  shown: number;
  total: number;
  /** What the entries are, plural: "servers", "skills". */
  noun: string;
}) {
  return (
    <div className="space-y-3" data-testid="catalogue-facets">
      <div className="grid gap-3 md:grid-cols-[2fr_1fr]">
        <label className="space-y-1">
          <span className="text-xs text-ink-muted">Search the catalogue</span>
          <Input
            type="search"
            className="w-full"
            placeholder="Name, publisher, what it does"
            value={filters.q}
            onChange={(event) => onChange({ ...filters, q: event.target.value })}
          />
        </label>
        <label className="space-y-1">
          <span className="text-xs text-ink-muted">Category</span>
          <Select
            className="w-full"
            value={filters.category}
            onChange={(event) => onChange({ ...filters, category: event.target.value })}
          >
            <option value="">All categories</option>
            {categories.map((category) => (
              <option key={category} value={category}>
                {categoryLabel(category)}
              </option>
            ))}
          </Select>
        </label>
      </div>
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm">
        {toggles.map((toggle) => (
          <label key={toggle.key} className="inline-flex items-center gap-2 pointer-coarse:min-h-11">
            <input
              type="checkbox"
              checked={toggled(filters, toggle.key)}
              onChange={(event) => onChange(withToggle(filters, toggle.key, event.target.checked))}
            />
            {toggle.label}
          </label>
        ))}
        <p className="ml-auto text-xs text-ink-muted" role="status">
          {shown === total ? `${total} ${noun}` : `${shown} of ${total} ${noun}`}
        </p>
      </div>
    </div>
  );
}
