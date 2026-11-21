// SPDX-License-Identifier: Apache-2.0
"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Button, Input, Select } from "@/components/ui";
import { api } from "@/lib/api";
import type { Org } from "@/lib/types";

const PAGE_SIZE = 50;

/**
 * The organisation switcher. With few organisations (`more` false) the session already holds them all
 * and a plain select lists them. With many, the server searches and pages (ADR 0206): the text goes to
 * `GET /orgs?q=`, "More" asks for the next page, and nothing is filtered in the browser. The current
 * organisation stays in the select even when the search does not return it.
 */
export function OrgSwitcher({
  org,
  orgs,
  more,
  onSelect,
}: {
  org: string;
  orgs: Org[];
  more: boolean;
  onSelect: (slug: string) => void;
}) {
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const found = useInfiniteQuery({
    queryKey: ["org-switcher", q],
    enabled: more,
    queryFn: ({ pageParam }) => api.orgs({ q: q || undefined, cursor: pageParam, limit: PAGE_SIZE }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => (last.meta.has_more ? (last.meta.next_cursor ?? undefined) : undefined),
  });
  const rows = more ? (found.data?.pages.flatMap((page) => page.items) ?? []) : orgs;
  const current = orgs.find((o) => o.slug === org);
  const listed = rows.some((o) => o.slug === org) || !current ? rows : [current, ...rows];

  return (
    <div className="flex items-center gap-2">
      <label className="flex items-center gap-2">
        <span className="text-ink-muted">Organisation</span>
        <Select
          aria-label="Current organisation"
          value={org}
          onChange={(event) => onSelect(event.target.value)}
          className="w-auto h-7 font-mono"
        >
          {listed.map((o) => (
            <option key={o.slug} value={o.slug}>
              {o.name}
            </option>
          ))}
        </Select>
      </label>
      {more && (
        <>
          <form
            className="flex items-center gap-1"
            onSubmit={(event) => {
              event.preventDefault();
              setQ(text.trim());
            }}
          >
            <Input
              type="search"
              aria-label="Find an organisation"
              placeholder="Find an organisation"
              value={text}
              onChange={(event) => setText(event.target.value)}
              className="h-7 w-44"
            />
          </form>
          {found.hasNextPage && (
            <Button size="sm" onClick={() => void found.fetchNextPage()} disabled={found.isFetchingNextPage}>
              More
            </Button>
          )}
        </>
      )}
    </div>
  );
}
