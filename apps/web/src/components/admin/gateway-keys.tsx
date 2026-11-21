// SPDX-License-Identifier: Apache-2.0
"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { useState } from "react";
import {
  Badge,
  Button,
  Card,
  Empty,
  ErrorNote,
  Input,
  Select,
  TBody,
  TD,
  TH,
  THead,
  TR,
  Table,
} from "@/components/ui";
import { api } from "@/lib/api";
import { shortDate, usd } from "@/lib/format";

const PAGE_SIZE = 50;
type StateFilter = "" | "active" | "revoked";

/**
 * The keys the model gateway minted for runs: which run and project each serves, what it spent of
 * its budget, and whether it is revoked. The secret itself is never shown nor asked for.
 * Platform administrators only: the API refuses the others.
 *
 * The server filters and pages (ADR 0206): the text and the state go to the query, "More" asks
 * for the next page by its cursor. Nothing is filtered in the browser.
 */
export function GatewayKeysCard() {
  const [text, setText] = useState("");
  const [filters, setFilters] = useState<{ q: string; state: StateFilter }>({
    q: "",
    state: "",
  });
  const keys = useInfiniteQuery({
    queryKey: ["gateway-keys", filters],
    queryFn: ({ pageParam }) =>
      api.gatewayKeys({
        q: filters.q || undefined,
        state: filters.state || undefined,
        cursor: pageParam,
        limit: PAGE_SIZE,
      }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) =>
      last.meta.has_more ? (last.meta.next_cursor ?? undefined) : undefined,
  });
  const rows = keys.data?.pages.flatMap((page) => page.items) ?? [];
  const filtered = filters.q !== "" || filters.state !== "";

  return (
    <Card title="Gateway keys">
      <form
        className="mb-3 flex flex-wrap items-center gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          setFilters({ ...filters, q: text.trim() });
        }}
      >
        <Input
          type="search"
          aria-label="Filter keys"
          placeholder="Key, run or project"
          value={text}
          onChange={(event) => setText(event.target.value)}
          className="max-w-xs"
        />
        <Select
          aria-label="Filter by state"
          value={filters.state}
          onChange={(event) =>
            setFilters({
              q: text.trim(),
              state: event.target.value as StateFilter,
            })
          }
        >
          <option value="">All states</option>
          <option value="active">Active</option>
          <option value="revoked">Revoked</option>
        </Select>
        <Button size="sm" type="submit">
          Filter
        </Button>
      </form>
      {keys.isError ? (
        <ErrorNote>
          {keys.error instanceof Error ? keys.error.message : "Refused"}
        </ErrorNote>
      ) : !keys.data ? (
        <p className="text-sm text-ink-muted">Reading the keys…</p>
      ) : rows.length === 0 ? (
        <Empty>
          {filtered ? "No key matches this filter" : "No gateway key"}
        </Empty>
      ) : (
        <Table aria-label="Gateway keys">
          <THead>
            <TR>
              <TH>Key</TH>
              <TH>Run</TH>
              <TH>Project</TH>
              <TH>Spent / budget</TH>
              <TH>Expires</TH>
              <TH>State</TH>
              <TH>Created</TH>
            </TR>
          </THead>
          <TBody>
            {rows.map((key) => (
              <TR key={key.key_id}>
                <TD className="font-mono text-xs">{key.key_id}</TD>
                <TD className="font-mono text-xs">{key.run_id ?? "—"}</TD>
                <TD>{key.project_slug ?? "—"}</TD>
                <TD>
                  {usd(key.spend_usd ?? 0)} /{" "}
                  {key.budget_usd == null ? "no budget" : usd(key.budget_usd)}
                </TD>
                <TD>{shortDate(key.expires_at)}</TD>
                <TD>
                  {key.revoked ? (
                    <Badge tone="danger">Revoked</Badge>
                  ) : (
                    <Badge tone="ok">Active</Badge>
                  )}
                </TD>
                <TD>{shortDate(key.created_at)}</TD>
              </TR>
            ))}
          </TBody>
        </Table>
      )}
      {keys.hasNextPage && (
        <div className="mt-3">
          <Button
            size="sm"
            onClick={() => void keys.fetchNextPage()}
            disabled={keys.isFetchingNextPage}
          >
            More
          </Button>
        </div>
      )}
    </Card>
  );
}
