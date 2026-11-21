/** A page of a list, as the server answers one (ADR 0206): the rows, and whether more follow. */
export function pageOf<T>(items: T[], next: string | null = null): { items: T[]; meta: { has_more: boolean; next_cursor: string | null } } {
  return { items, meta: { has_more: next !== null, next_cursor: next } };
}

/** What the server does with `kind`, `type` and `q` on the connectors list: the mock answers like it. */
export function byFilters<T extends { name?: string; kind?: string; type?: string }>(rows: T[]) {
  return async (_org: string, params: { kind?: string; type?: string; q?: string } = {}) =>
    pageOf(
      rows.filter(
        (row) =>
          (!params.kind || row.kind === params.kind) &&
          (!params.type || row.type === params.type) &&
          (!params.q || (row.name ?? "").toLowerCase().includes(params.q.toLowerCase())),
      ),
    );
}
