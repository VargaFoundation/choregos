// SPDX-License-Identifier: Apache-2.0
"use client";

import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

/** The page size of the people tables; the server caps it at 200 (ADR 0206). */
export const MEMBERS_PAGE_SIZE = 50;

/**
 * The organisation's memberships, a page at a time (ADR 0206): `rows` is what has been read so far,
 * `hasMore` whether the server holds more. The key starts with `["members", org]`, which is what the
 * gestures that add or remove a member invalidate.
 */
export function useMemberPages(org: string, opts: { project?: string; enabled?: boolean } = {}) {
  const pages = useInfiniteQuery({
    queryKey: ["members", org, "pages", opts.project ?? ""],
    queryFn: ({ pageParam }) => api.members(org, { project: opts.project, cursor: pageParam, limit: MEMBERS_PAGE_SIZE }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => (last.meta.has_more ? (last.meta.next_cursor ?? undefined) : undefined),
    enabled: opts.enabled ?? true,
  });
  return { ...pages, rows: pages.data?.pages.flatMap((page) => page.items) ?? [], hasMore: pages.hasNextPage };
}

/**
 * The memberships a picker offers: the server filters on the e-mail (`q`) and the first page (200 at most)
 * is what a select shows, so a long organisation is searched, never loaded whole into the browser.
 */
export function useMemberPicker(org: string, q: string, enabled = true) {
  const picked = useQuery({
    queryKey: ["members", org, "pick", q],
    queryFn: () => api.members(org, { q: q || undefined, limit: 200 }),
    enabled,
  });
  return { ...picked, rows: picked.data?.items ?? [], hasMore: picked.data?.meta.has_more ?? false };
}
