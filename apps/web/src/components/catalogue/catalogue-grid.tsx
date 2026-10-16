// SPDX-License-Identifier: Apache-2.0
import type { ReactNode } from "react";
import { Empty } from "@/components/ui";

/** The grid of a catalogue's cards, and what it says when a filter leaves none (ADR 0065). */
export function CatalogueGrid<T>({
  items,
  render,
  keyOf,
  empty,
}: {
  items: T[];
  render: (item: T) => ReactNode;
  keyOf: (item: T) => string;
  empty: string;
}) {
  if (items.length === 0) return <Empty title="Nothing matches">{empty}</Empty>;
  return (
    <ul className="grid gap-3 md:grid-cols-2 xl:grid-cols-3" data-testid="catalogue-grid">
      {items.map((item) => (
        <li key={keyOf(item)} className="flex">
          {render(item)}
        </li>
      ))}
    </ul>
  );
}

/**
 * One catalogue entry. The title, the publisher and the description are data (a vendor's words):
 * they keep their case. `badges` says what the entry is, `facts` what it needs, `footer` what can be done.
 */
export function CatalogueCard({
  id,
  title,
  publisher,
  description,
  badges,
  facts,
  footer,
}: {
  id: string;
  title: string;
  publisher: string;
  description: string;
  badges?: ReactNode;
  facts?: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <article
      className="raised flex w-full flex-col gap-3 rounded-md border border-line p-4"
      data-testid={`entry-${id}`}
      aria-labelledby={`entry-title-${id}`}
    >
      <header className="space-y-1">
        <h2 id={`entry-title-${id}`} className="text-sm font-semibold text-ink" data-donnee>
          {title}
        </h2>
        <p className="text-xs text-ink-muted" data-donnee>
          {publisher}
        </p>
      </header>
      {badges && <div className="flex flex-wrap gap-1.5">{badges}</div>}
      <p className="text-sm text-ink-muted" data-donnee>
        {description}
      </p>
      {facts && <div className="space-y-1 text-xs text-ink-muted">{facts}</div>}
      {footer && <footer className="mt-auto space-y-2 pt-1">{footer}</footer>}
    </article>
  );
}
