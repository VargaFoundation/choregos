// SPDX-License-Identifier: Apache-2.0
import type { PageMeta } from "@choregos/contracts/api";

/**
 * Pages a mock list the way the API does (ADR 0206): the rows in the order the server reads them,
 * `limit` of them after the `cursor`, and `meta` saying whether another page follows. The cursor is
 * opaque to the page; here it is the sort key of the last row, so that two rows sharing a key
 * are told apart by their position.
 */
export type MockPage<T> = { items: T[]; meta: { has_more: boolean; next_cursor: string | null } };

export function mockPage<T>(
  rows: readonly T[],
  path: string,
  key: (row: T) => string | number,
  options: { descending?: boolean } = {},
): MockPage<T> {
  const query = new URLSearchParams(path.split("?")[1] ?? "");
  const limit = Math.min(Math.max(Number(query.get("limit") ?? 50) || 50, 1), 200);
  const sign = options.descending ? -1 : 1;
  const ordered = rows
    .map((row, position) => ({ row, position }))
    .sort((a, b) => {
      const ka = key(a.row);
      const kb = key(b.row);
      return (ka < kb ? -1 : ka > kb ? 1 : 0) * sign || a.position - b.position;
    })
    .map((entry) => entry.row);
  const cursor = query.get("cursor");
  // Lenient on purpose: the demo table evaluates every list for each request, so a cursor another
  // route issued must not throw here.
  let start = 0;
  try {
    start = cursor ? Number(atob(cursor)) : 0;
  } catch {
    start = 0;
  }
  if (!Number.isInteger(start) || start < 0) start = 0;
  const items = ordered.slice(start, start + limit);
  const more = start + limit < ordered.length;
  return { items, meta: { has_more: more, next_cursor: more ? btoa(String(start + limit)) : null } };
}

/**
 * A page of `items` the way the server pages (ADR 0206): `limit` (default 50) rows after the opaque
 * `cursor`, `meta.has_more` and a `meta.next_cursor` to pass back. `items` is already in the route's order.
 * The cursor of the mock is the offset, encoded: only the mock reads it.
 */
export function pageOf<T>(items: readonly T[], query: string | URLSearchParams): { items: T[]; meta: PageMeta } {
  const search = typeof query === "string" ? new URLSearchParams(query) : query;
  const limit = Math.max(1, Math.min(200, Number(search.get("limit") ?? 50) || 50));
  const cursor = search.get("cursor");
  const start = cursor ? Number(atob(cursor)) || 0 : 0;
  const slice = items.slice(start, start + limit);
  const more = start + limit < items.length;
  return {
    items: structuredClone(slice),
    meta: { has_more: more, next_cursor: more ? btoa(String(start + limit)) : null },
  };
}
