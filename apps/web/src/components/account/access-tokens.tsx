// SPDX-License-Identifier: Apache-2.0
"use client";

import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { KeyRound } from "lucide-react";
import { Badge, Button, Card, Empty, ErrorNote, Input, ReadState } from "@/components/ui";
import { ConfirmedGesture } from "@/components/confirmed-gesture";
import { api } from "@/lib/api";
import { relative, shortDate } from "@/lib/format";

/**
 * The signed-in person's access tokens: the list, their creation and their revocation. They belong to
 * the person, not to the organisation: the card lives on "My account" (`/account`), not in the
 * administration.
 */
export function AccessTokensCard({ className }: { className?: string }) {
  const queryClient = useQueryClient();
  const tokens = useQuery({ queryKey: ["tokens"], queryFn: () => api.myTokens() });
  const [error, setError] = useState<string | null>(null);
  const [tokenName, setTokenName] = useState("");
  const [tokenClair, setTokenClair] = useState<string | null>(null);
  // The instant the page opened: a token is said expired against it, not against each render.
  const [openedAt] = useState(() => Date.now());

  async function frapper() {
    setError(null);
    try {
      const created = await api.createToken({ name: tokenName || "cli", expires_in_days: 90 });
      setTokenClair(created.token);
      setTokenName("");
      void queryClient.invalidateQueries({ queryKey: ["tokens"] });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "token refused");
    }
  }

  // One token, one name, one place (S23-14): "API token", "token for Claude Code" and "MCP client" were
  // three names for the same object. Here, the list of ALL the person's tokens, and their revocation;
  // the integrations and the agents point here.
  return (
    <Card title="Access tokens" id="tokens" className={className}>
      <p className="mb-3 text-xs text-ink-muted">
        Every token you hold: for the API and the CLI, or for an AI client at the MCP door (created from{" "}
        <Link href="/integrations">Integrations</Link> or <Link href="/agents">Agents</Link>).
      </p>
      {error && <ErrorNote>{error}</ErrorNote>}
      {/* The clear text exists only here, once: it is never stored or shown again. */}
      {tokenClair && (
        <div className="mb-3 border border-line border-l-2 border-l-accent bg-surface p-3 text-sm">
          <p className="text-xs text-ink-muted">Copy it now — it will not be shown again</p>
          <code className="block break-all font-mono text-xs">{tokenClair}</code>
        </div>
      )}
      <form
        className="mb-4 flex flex-wrap items-end gap-2 text-sm"
        onSubmit={(event) => {
          event.preventDefault();
          void frapper();
        }}
      >
        <label className="flex min-w-0 flex-1 flex-col gap-1">
          <span className="text-xs text-ink-muted">Token name</span>
          <Input
            value={tokenName}
            onChange={(event) => setTokenName(event.target.value)}
            placeholder="cli"
            className="min-w-0 flex-1"
          />
        </label>
        <Button size="sm" tone="primary" type="submit">
          Create an API token (90 days)
        </Button>
      </form>
      <ul aria-label="Your access tokens" className="divide-y divide-line">
        {(tokens.data ?? []).map((token) => {
          const expired = token.expires_at != null && new Date(token.expires_at).getTime() <= openedAt;
          return (
            <li key={token.id} data-testid="token" className="flex items-start gap-3 py-2.5 first:pt-0 last:pb-0">
              <KeyRound aria-hidden className="mt-0.5 size-4 shrink-0 text-ink-muted" strokeWidth={1.75} />
              <div className="min-w-0 flex-1">
                <p className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="font-mono" data-verbatim>
                    {token.name}
                  </span>
                  {expired && (
                    <Badge tone="warn" variant="soft">
                      Expired
                    </Badge>
                  )}
                </p>
                <p className="text-xs text-ink-muted">
                  {tokenScopeSentence(token.scopes)} · {expired ? "expired" : "expires"}{" "}
                  {token.expires_at ? shortDate(token.expires_at) : "never"} · last used{" "}
                  {token.last_used_at ? relative(token.last_used_at) : "never"}
                  {token.last_client && (
                    <>
                      {" "}
                      by <span data-verbatim>{token.last_client}</span>
                    </>
                  )}
                </p>
              </div>
              <ConfirmedGesture
                tonDuBouton="danger"
                question={`Revoke ${token.name}? Every client using it is refused from its next call — a token does not come back.`}
                confirmer={`Revoke ${token.name}`}
                action={async () => {
                  await api.revokeToken(token.id);
                  await queryClient.invalidateQueries({ queryKey: ["tokens"] });
                }}
              >
                Revoke
              </ConfirmedGesture>
            </li>
          );
        })}
      </ul>
      <ReadState lecture={tokens} quoi="your access tokens" />
      {tokens.data?.length === 0 && <Empty>No token</Empty>}
    </Card>
  );
}

/** What a token opens, in words: the API (and the CLI), or an AI client's MCP door. */
function tokenScopeSentence(scopes: string[] | null | undefined): string {
  const scopeList = scopes ?? [];
  if (scopeList.length > 0 && scopeList.every((s) => s.startsWith("mcp:")))
    return scopeList.includes("mcp:write") ? "AI client, can open work items" : "AI client, read-only";
  return "API and CLI";
}
