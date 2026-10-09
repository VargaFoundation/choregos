// SPDX-License-Identifier: Apache-2.0
"use client";

import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button, Card, Empty, ErrorNote, EtatDeLecture } from "@/components/ui";
import { api } from "@/lib/api";
import { shortDate } from "@/lib/format";
import { useSession } from "@/lib/session";
import { GesteConfirme } from "@/components/geste-confirme";

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

        {/* Un jeton, un nom, un endroit (S23-14) : « API token » ici, « token for Claude Code » dans les
            clients, « MCP client » dans les agents — trois noms pour le même objet, sous deux clés de
            cache. Ici la liste de TOUS les jetons de la personne, et leur révocation ; les deux autres
            pages renvoient ici. */}
        <Card title="my access tokens">
          <p className="mb-3 text-xs text-ink-muted" id="jetons">
            Every token you hold: for the API and the CLI, or for an AI client at the MCP door (created from{" "}
            <Link href="/integrations">AI clients</Link> or <Link href="/agents">agents</Link>).
          </p>
          {/* Le clair n'existe qu'ici, une fois : il n'est jamais stocké ni réaffiché. */}
          {tokenClair && (
            <div className="mb-3 border border-line border-l-2 border-l-accent bg-surface p-3 text-sm">
              <p className="text-xs text-ink-muted">copy it now — it will not be shown again</p>
              <code className="block break-all font-mono text-xs">{tokenClair}</code>
            </div>
          )}
          <form
            className="mb-3 flex flex-wrap items-end gap-2 text-sm"
            onSubmit={(event) => {
              event.preventDefault();
              void frapper();
            }}
          >
            <label className="flex min-w-0 flex-1 flex-col gap-1">
              <span className="text-xs text-ink-muted">token name</span>
              <input
                value={tokenName}
                onChange={(event) => setTokenName(event.target.value)}
                placeholder="cli"
                className="min-w-0 flex-1 rounded border border-line bg-surface px-2 py-1"
              />
            </label>
            <Button size="sm" tone="primary" type="submit">
              create an API token (90 days)
            </Button>
          </form>
          <ul className="space-y-1 text-xs">
            {(tokens.data ?? []).map((token) => (
              <li key={token.id} className="flex items-center justify-between gap-2">
                <span>
                  <span className="font-mono">{token.name}</span>
                  <span className="text-ink-muted">
                    {" "}
                    · {porteeDuJeton(token.scopes)} · expires {token.expires_at ? shortDate(token.expires_at) : "never"} · last used{" "}
                    {token.last_used_at ? shortDate(token.last_used_at) : "—"}
                  </span>
                </span>
                <GesteConfirme
                  tonDuBouton="danger"
                  question={`revoke ${token.name}? every client using it is refused from its next call — a token does not come back.`}
                  confirmer={`revoke ${token.name}`}
                  action={async () => {
                    await api.revokeToken(token.id);
                    await queryClient.invalidateQueries({ queryKey: ["tokens"] });
                  }}
                >
                  revoke
                </GesteConfirme>
              </li>
            ))}
          </ul>
          <EtatDeLecture lecture={tokens} quoi="your access tokens" />
          {tokens.data?.length === 0 && <Empty>no token</Empty>}
        </Card>
      </div>
    </div>
  );
}

/** Ce qu'un jeton ouvre, en mots : l'API (et la CLI), ou la porte MCP d'un client d'IA. */
function porteeDuJeton(scopes: string[] | null | undefined): string {
  const liste = scopes ?? [];
  if (liste.length > 0 && liste.every((s) => s.startsWith("mcp:")))
    return liste.includes("mcp:write") ? "AI client, can open work items" : "AI client, read-only";
  return "API and CLI";
}
