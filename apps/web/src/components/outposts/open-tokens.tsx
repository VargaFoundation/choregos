// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ConfirmedGesture } from "@/components/confirmed-gesture";
import { Card, ErrorNote, Table, TBody, TD, TH, THead, TR } from "@/components/ui";
import { api } from "@/lib/api";
import { relative, shortDate } from "@/lib/format";
import { refusalOf } from "@/lib/outposts";

/**
 * The enrolment tokens of an outpost that can still enrol an instance (S65-24): who issued each and
 * until when, never the token itself, whose digest is all the platform keeps. A token pasted in the
 * wrong place is cancelled here rather than left to live out its fifteen minutes. Nothing is shown
 * while there is none: the card is about what is still open.
 */
export function OpenTokens({ org, slug }: { org: string; slug: string }) {
  const client = useQueryClient();
  const tokens = useQuery({
    queryKey: ["outpost-enrolments", org, slug],
    queryFn: () => api.outpostEnrolments(org, slug),
  });
  if (tokens.error) {
    return (
      <Card title="Open enrolment tokens">
        <ErrorNote>{tokens.error instanceof Error ? tokens.error.message : String(tokens.error)}</ErrorNote>
      </Card>
    );
  }
  const items = tokens.data?.items ?? [];
  if (items.length === 0) return null;
  return (
    <Card title="Open enrolment tokens" data-testid="outpost-open-tokens">
      <p className="mb-3 text-sm text-ink-muted">
        These tokens can still enrol an instance. Each works once and expires on its own; cancel one that should not be used.
      </p>
      <Table>
        <THead>
          <TR>
            <TH>Token</TH>
            <TH>Issued</TH>
            <TH>Expires</TH>
            <TH>
              <span className="sr-only">Actions</span>
            </TH>
          </TR>
        </THead>
        <TBody>
          {items.map((token) => (
            <TR key={token.id} data-testid={`open-token-${token.id}`}>
              <TD className="font-mono text-xs" data-verbatim>
                {token.id}
              </TD>
              <TD className="text-xs">
                {shortDate(token.created_at)}
                {token.created_by && (
                  <>
                    {" "}
                    by <span data-verbatim>{token.created_by}</span>
                  </>
                )}
              </TD>
              <TD className="text-xs" title={shortDate(token.expires_at)}>
                {relative(token.expires_at)}
              </TD>
              <TD>
                <ConfirmedGesture
                  tonDuBouton="danger"
                  question="Cancel this token? It enrols nothing from now on."
                  confirmer="Yes, cancel the token"
                  action={async () => {
                    try {
                      await api.cancelOutpostEnrolment(org, slug, token.id);
                    } catch (cause) {
                      throw new Error(refusalOf(cause).detail);
                    }
                    await client.invalidateQueries({ queryKey: ["outpost-enrolments", org, slug] });
                  }}
                >
                  Cancel
                </ConfirmedGesture>
              </TD>
            </TR>
          ))}
        </TBody>
      </Table>
    </Card>
  );
}
