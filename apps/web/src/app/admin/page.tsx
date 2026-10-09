// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button, Card, Empty, ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";
import { shortDate } from "@/lib/format";
import { useSession } from "@/lib/session";

/**
 * L'administration, vue d'ensemble : la session et mes jetons d'API. Les membres, l'audit, la
 * plateforme, l'édition et les sections des greffons ont leur sous-page (ADR 0032).
 */
export default function AdminPage() {
  const { me } = useSession();
  const queryClient = useQueryClient();
  const tokens = useQuery({ queryKey: ["tokens"], queryFn: () => api.myTokens() });
  const [error, setError] = useState<string | null>(null);
  const [tokenName, setTokenName] = useState("");
  const [tokenClair, setTokenClair] = useState<string | null>(null);

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

  return (
    <div className="space-y-4">
      {error && <ErrorNote>{error}</ErrorNote>}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="session">
          <p className="text-sm">
            {me?.display_name} — {me?.email}
          </p>
          <ul className="mt-2 space-y-1 text-xs text-ink-muted">
            {(me?.memberships ?? []).map((membership, index) => (
              <li key={index}>
                {membership.org}
                {membership.project_slug ? `/${membership.project_slug}` : ""} : {membership.role}
              </li>
            ))}
          </ul>
        </Card>

        <Card title="my API tokens">
          {/* Le clair n'existe qu'ici, une fois : il n'est jamais stocké ni réaffiché. */}
          {tokenClair && (
            <div className="mb-3 border border-line border-l-2 border-l-accent bg-surface p-3 text-sm">
              <p className="text-xs text-ink-muted">copy it now — it will not be shown again</p>
              <code className="block break-all font-mono text-xs">{tokenClair}</code>
            </div>
          )}
          <form
            className="mb-3 flex flex-wrap items-center gap-2 text-sm"
            onSubmit={(event) => {
              event.preventDefault();
              void frapper();
            }}
          >
            <input
              aria-label="token name"
              value={tokenName}
              onChange={(event) => setTokenName(event.target.value)}
              placeholder="cli"
              className="min-w-0 flex-1 rounded border border-line bg-surface px-2 py-1"
            />
            <Button size="sm" tone="primary" type="submit">
              mint a token (90 days)
            </Button>
          </form>
          <ul className="space-y-1 text-xs">
            {(tokens.data ?? []).map((token) => (
              <li key={token.id} className="flex items-center justify-between gap-2">
                <span>
                  <span className="font-mono">{token.name}</span>
                  <span className="text-ink-muted">
                    {" "}
                    · expires {token.expires_at ? shortDate(token.expires_at) : "never"} · last used{" "}
                    {token.last_used_at ? shortDate(token.last_used_at) : "—"}
                  </span>
                </span>
                <Button
                  size="sm"
                  tone="danger"
                  onClick={() =>
                    void api.revokeToken(token.id).then(() => queryClient.invalidateQueries({ queryKey: ["tokens"] }))
                  }
                >
                  revoke
                </Button>
              </li>
            ))}
          </ul>
          {(tokens.data ?? []).length === 0 && <Empty>no token</Empty>}
        </Card>

      </div>
    </div>
  );
}
