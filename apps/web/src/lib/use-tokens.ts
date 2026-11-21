// SPDX-License-Identifier: Apache-2.0
"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { api } from "./api";

const PAGE_SIZE = 50;

/**
 * The signed-in person's tokens, paged by the server (ADR 0206): `kind` and `q` go to the query, `fetchNextPage`
 * asks for the next page by its cursor, and nothing is filtered in the browser. The key starts with `tokens`
 * so that invalidating `["tokens"]` after a creation or a revocation refreshes every list.
 */
export function useTokens(filters: { kind?: "mcp" | "api"; q?: string } = {}) {
  const query = useInfiniteQuery({
    queryKey: ["tokens", "list", filters],
    queryFn: ({ pageParam }) =>
      api.myTokens({ kind: filters.kind, q: filters.q || undefined, cursor: pageParam, limit: PAGE_SIZE }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => (last.meta.has_more ? (last.meta.next_cursor ?? undefined) : undefined),
  });
  return { ...query, rows: query.data?.pages.flatMap((page) => page.items) ?? [] };
}
