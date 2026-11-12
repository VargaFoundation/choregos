// SPDX-License-Identifier: Apache-2.0
"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Badge, Button, Card, Code, Empty, ErrorNote, Select, Table, TBody, TD, TH, THead, TR } from "@/components/ui";
import { api } from "@/lib/api";
import { relative, shortDate } from "@/lib/format";
import { CALL_STATES, CALL_TONE, callSubject, stateLabel } from "@/lib/outposts";
import type { OutpostCallRecord, OutpostCallState } from "@/lib/types";

const PAGE = 50;

/** A digest is compared, never read: its first characters, and the whole of it on hover. */
function Digest({ value }: { value: string | null }) {
  if (!value) return <span className="text-ink-muted">—</span>;
  return (
    <span className="font-mono text-xs" title={value}>
      {value.slice(0, 12)}
    </span>
  );
}

/** How a call ended, in the API's own codes where it has one: an outcome, or the code of a refusal. */
function Ending({ call }: { call: OutpostCallRecord }) {
  if (call.refusal) return <Code>{call.refusal}</Code>;
  if (call.outcome) {
    return (
      <span>
        {stateLabel(call.outcome)}
        {call.is_error ? <span className="text-ink-muted">, the server answered an error</span> : null}
      </span>
    );
  }
  return <span className="text-ink-muted">—</span>;
}

/**
 * The recent calls routed to an outpost, newest first: how each ended, and the code of its refusal
 * when the outpost's own policy refused it. Digests only: the platform keeps no arguments and no
 * result of a call, so there is none to show.
 */
export function OutpostCalls({ org, slug }: { org: string; slug: string }) {
  const [state, setState] = useState<OutpostCallState | "">("");
  const pages = useInfiniteQuery({
    queryKey: ["outpost-calls", org, slug, state],
    queryFn: ({ pageParam }) => api.outpostCalls(org, slug, { state: state || undefined, cursor: pageParam, limit: PAGE }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => (last.meta.has_more ? (last.meta.next_cursor ?? undefined) : undefined),
  });
  const calls = pages.data?.pages.flatMap((page) => page.items) ?? [];
  return (
    <Card
      title="Recent calls"
      data-testid="outpost-calls"
      action={
        <label className="flex items-center gap-2 text-sm">
          <span className="text-xs text-ink-muted">State</span>
          <Select aria-label="State" value={state} onChange={(event) => setState(event.target.value as OutpostCallState | "")} className="w-auto">
            <option value="">Any</option>
            {CALL_STATES.map((name) => (
              <option key={name} value={name}>
                {stateLabel(name)}
              </option>
            ))}
          </Select>
        </label>
      }
    >
      <p className="mb-3 text-xs text-ink-muted">
        Digests only: the platform keeps no arguments and no result of a call. The arguments&apos; digest is the one the
        outpost&apos;s own audit log writes, so the two can be compared.
      </p>
      {pages.error && <ErrorNote>{pages.error instanceof Error ? pages.error.message : String(pages.error)}</ErrorNote>}
      {pages.data && calls.length === 0 && <Empty>{state ? "No call in this state." : "No call was routed to this outpost yet."}</Empty>}
      {calls.length > 0 && (
        <Table>
          <THead>
            <TR>
              <TH>When</TH>
              <TH>Call</TH>
              <TH>Endpoint</TH>
              <TH>Connector</TH>
              <TH>State</TH>
              <TH>Outcome or refusal</TH>
              <TH>Arguments</TH>
              <TH>Result</TH>
              <TH>For</TH>
            </TR>
          </THead>
          <TBody>
            {calls.map((call) => (
              <TR key={call.id} data-testid={`call-${call.id}`}>
                <TD className="text-xs whitespace-nowrap" title={shortDate(call.created_at)}>
                  {relative(call.created_at)}
                </TD>
                <TD>
                  <span className={call.tool ? "font-mono text-xs" : undefined} data-verbatim={call.tool ? true : undefined}>
                    {callSubject(call)}
                  </span>
                  {call.write && (
                    <Badge tone="warn" variant="soft" dot={false} className="ml-2">
                      Write
                    </Badge>
                  )}
                </TD>
                <TD className="font-mono text-xs" data-verbatim>
                  {call.endpoint}
                </TD>
                <TD className="text-xs" data-verbatim>
                  {call.connector ?? "—"}
                </TD>
                <TD>
                  <Badge tone={CALL_TONE[call.state]}>{stateLabel(call.state)}</Badge>
                </TD>
                <TD className="text-xs">
                  <Ending call={call} />
                </TD>
                <TD>
                  <Digest value={call.arguments_sha256} />
                </TD>
                <TD>
                  <Digest value={call.result_sha256} />
                </TD>
                <TD className="text-xs" data-verbatim>
                  {call.project ?? "—"}
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      )}
      {pages.hasNextPage && (
        <div className="mt-3">
          <Button size="sm" onClick={() => void pages.fetchNextPage()} disabled={pages.isFetchingNextPage}>
            {pages.isFetchingNextPage ? "Reading…" : "Older calls"}
          </Button>
        </div>
      )}
    </Card>
  );
}
