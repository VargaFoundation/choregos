// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { ConfirmedGesture } from "@/components/confirmed-gesture";
import { Badge, Button, Card, Empty, ErrorNote, Input, ReadState } from "@/components/ui";
import { assistantsApi } from "@/lib/assistants-api";
import { relative } from "@/lib/format";

/** Hands the browser to the source's consent page for `connector` (what a connect link opens). */
export function startConnection(connector: string, org: string): Promise<void> {
  return assistantsApi.connectAccount(connector, org).then((started) => {
    window.location.assign(started.authorization_url);
  });
}

/** Where a connect link lands: asks the API for the authorization URL and goes there. */
export function ConnectAccountRedirect({ connector, org }: { connector: string; org: string }) {
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!org) return;
    startConnection(connector, org).catch((cause) =>
      setError(cause instanceof Error ? cause.message : "Could not start the connection"),
    );
  }, [connector, org]);
  if (!org) return <ErrorNote>The link names no organisation.</ErrorNote>;
  if (error) return <ErrorNote>{error}</ErrorNote>;
  return <p className="text-sm text-ink-muted">Taking you to {connector} to ask for your consent…</p>;
}

/**
 * My connected accounts: what the assistants may read as me. Connect goes through the authorization
 * URL the API returns; revoking forgets the token. `org` narrows the list to one organisation.
 */
export function ConnectedAccounts({ org }: { org?: string }) {
  const client = useQueryClient();
  const accounts = useQuery({ queryKey: ["connected-accounts"], queryFn: assistantsApi.connectedAccounts });
  const [connector, setConnector] = useState("");
  const [error, setError] = useState<string | null>(null);
  const items = (accounts.data?.items ?? []).filter((a) => !org || a.org === org);
  async function connect(name: string, where: string) {
    setError(null);
    try {
      await startConnection(name, where);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not start the connection");
    }
  }
  return (
    <div className="space-y-4">
      <p className="text-sm text-ink-muted">
        Accounts you connected so that an assistant can read your own data at a source. Nothing is read under
        another identity.
      </p>
      <ReadState lecture={accounts} quoi="your connected accounts" />
      {error && <ErrorNote>{error}</ErrorNote>}
      {accounts.data && items.length === 0 && <Empty title="No account connected">You have connected none.</Empty>}
      {items.map((a) => (
        <Card key={`${a.org}/${a.connector}`} padding="sm" title={a.connector} verbatimTitle eyebrow={a.org}>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="space-y-1 text-sm">
              <Badge tone={a.status === "connected" ? "succeeded" : "waiting"}>
                {a.status === "connected" ? "Connected" : "Reconnect needed"}
              </Badge>
              {a.subject && <p className="text-ink-muted">as {a.subject}</p>}
              <p className="text-xs text-ink-muted">
                {a.scopes.length ? `Scopes: ${a.scopes.join(", ")}` : "No scope"}
                {a.last_used_at ? ` · last used ${relative(a.last_used_at)}` : ""}
              </p>
            </div>
            <div className="flex gap-2">
              {a.status !== "connected" && (
                <Button size="sm" tone="primary" onClick={() => void connect(a.connector, a.org)}>
                  Reconnect
                </Button>
              )}
              <ConfirmedGesture
                question={`Revoke the connection to ${a.connector}? Assistants will no longer read your data there.`}
                confirmer="Revoke"
                action={async () => {
                  await assistantsApi.disconnectAccount(a.connector, a.org);
                  await client.invalidateQueries({ queryKey: ["connected-accounts"] });
                }}
              >
                Revoke
              </ConfirmedGesture>
            </div>
          </div>
        </Card>
      ))}
      {org && (
        <form
          className="flex items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (connector.trim()) void connect(connector.trim(), org);
          }}
        >
          <label className="space-y-1 text-xs text-ink-muted">
            Connector to connect
            <Input value={connector} onChange={(e) => setConnector(e.target.value)} placeholder="bank-accounts" />
          </label>
          <Button type="submit" disabled={!connector.trim()}>
            Connect
          </Button>
        </form>
      )}
    </div>
  );
}
