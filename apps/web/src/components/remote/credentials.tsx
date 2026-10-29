// SPDX-License-Identifier: Apache-2.0
"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { GesteConfirme } from "@/components/geste-confirme";
import { Alert, Badge, Button, Card, Empty, ErrorNote, EtatDeLecture, Field, Input, Select, Table, TBody, TD, TH, THead, TR } from "@/components/ui";
import { api } from "@/lib/api";
import { relative } from "@/lib/format";
import type { RemoteCredential, RemoteCredentialCreate } from "@/lib/types";

export const CREDENTIAL_TYPES: Record<RemoteCredential["type"], string> = {
  oauth2_authorization_code: "OAuth (a person authorises)",
  bearer: "Bearer token",
  api_key: "API key (in a header)",
  none: "None",
};

/** An HTTP header name (RFC 9110 `token`): where an API key travels, never the key itself. */
const HEADER_NAME = /^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,64}$/;

const STATUS_TONE = { connected: "ok", pending: "waiting", error: "danger", revoked: "neutral" } as const;

/** What a person reads of a status; `pending` is the one that asks for a gesture. */
export function statusLine(credential: RemoteCredential): string {
  switch (credential.status) {
    case "pending":
      return "Registered, not yet authorised";
    case "connected":
      return credential.authorised_as ? `Acts as ${credential.authorised_as}` : "Connected";
    case "error":
      return credential.last_error ?? "The last refresh failed";
    default:
      return "Revoked";
  }
}

/**
 * The organisation's credentials for remote agents (ADR 0062). An OAuth credential is registered from
 * an agent card, then a person authorises it: the remote agent acts with THAT person's permissions,
 * which this page says in the open. No secret is ever shown; reconnecting replaces the sealed one.
 */
export function RemoteCredentials({
  org,
  connected,
  navigate = (url: string) => window.location.assign(url),
}: {
  org: string;
  /** The credential the provider just redirected back from. */
  connected?: string | null;
  navigate?: (url: string) => void;
}) {
  const client = useQueryClient();
  const credentials = useQuery({ queryKey: ["remote-credentials", org], queryFn: () => api.remoteCredentials(org) });
  const [error, setError] = useState<string | null>(null);
  const refresh = () => client.invalidateQueries({ queryKey: ["remote-credentials", org] });

  const authorize = useMutation({
    mutationFn: (name: string) => api.authorizeRemoteCredential(org, name),
    onSuccess: (authorization) => navigate(authorization.redirect_url),
    onError: (cause) => setError(cause instanceof Error ? cause.message : "refused"),
  });

  return (
    <div className="space-y-4">
      {connected && (
        <div data-testid="connected-note">
          <Alert tone="ok" title="Connected">
            The credential <span className="font-mono">{connected}</span> is authorised. Its refresh token is sealed and is not shown.
          </Alert>
        </div>
      )}
      <Card title="Remote agent credentials">
        <p className="mb-3 max-w-3xl text-xs text-ink-muted">
          A remote agent runs elsewhere and acts with the rights of whoever authorised its credential. Use a dedicated account with only the
          access the agent should have.
        </p>
        <EtatDeLecture lecture={credentials} quoi="the remote credentials" />
        {credentials.data?.length === 0 && (
          <Empty title="No credential yet">Add one below, then register the agent from Agents, New, Remote agent.</Empty>
        )}
        {credentials.data && credentials.data.length > 0 && (
          <Table aria-label="Remote credentials" data-testid="remote-credentials">
            <THead>
              <TR>
                <TH>Name</TH>
                <TH>Type</TH>
                <TH>Status</TH>
                <TH>Host</TH>
                <TH>Refreshed</TH>
                <TH align="right">Actions</TH>
              </TR>
            </THead>
            <TBody>
              {credentials.data.map((credential) => (
                <TR key={credential.name}>
                  <TD className="font-mono text-xs" data-donnee>
                    {credential.name}
                  </TD>
                  <TD className="text-xs">
                    {CREDENTIAL_TYPES[credential.type]}
                    {credential.header && (
                      <span className="block font-mono" data-testid={`header-${credential.name}`}>
                        {credential.header}
                      </span>
                    )}
                  </TD>
                  <TD>
                    <Badge tone={STATUS_TONE[credential.status]}>{credential.status}</Badge>
                    <p className="mt-1 text-xs text-ink-muted" data-testid={`status-${credential.name}`}>
                      {statusLine(credential)}
                    </p>
                  </TD>
                  <TD className="font-mono text-xs">{credential.host ?? "—"}</TD>
                  <TD className="text-xs">{credential.last_refresh_at ? relative(credential.last_refresh_at) : "never"}</TD>
                  <TD align="right">
                    <span className="inline-flex flex-wrap items-start justify-end gap-2">
                      {credential.type === "oauth2_authorization_code" && credential.status !== "revoked" && (
                        <Button
                          size="sm"
                          tone={credential.status === "connected" ? "default" : "primary"}
                          disabled={authorize.isPending}
                          onClick={() => {
                            setError(null);
                            authorize.mutate(credential.name);
                          }}
                        >
                          {credential.status === "connected" ? "Reconnect" : "Connect"}
                        </Button>
                      )}
                      {credential.status !== "revoked" && (
                        <GesteConfirme
                          ton="danger"
                          question={`Revoke ${credential.name}? Its sealed secrets are deleted and no run can use it again. Agents that name it stop until another credential is set.`}
                          confirmer="Revoke the credential"
                          action={() => api.revokeRemoteCredential(org, credential.name)}
                          onFait={() => void refresh()}
                        >
                          Revoke
                        </GesteConfirme>
                      )}
                    </span>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
        {error && <ErrorNote>{error}</ErrorNote>}
      </Card>
      <AddCredential org={org} onAdded={refresh} />
    </div>
  );
}

function AddCredential({ org, onAdded }: { org: string; onAdded: () => void }) {
  const [name, setName] = useState("");
  const [type, setType] = useState<RemoteCredential["type"]>("oauth2_authorization_code");
  const [cardUrl, setCardUrl] = useState("");
  const [token, setToken] = useState("");
  const [header, setHeader] = useState("");
  const [error, setError] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: (body: RemoteCredentialCreate) => api.createRemoteCredential(org, body),
    onSuccess: () => {
      setName("");
      setCardUrl("");
      setToken("");
      setHeader("");
      setError(null);
      onAdded();
    },
    onError: (cause) => setError(cause instanceof Error ? cause.message : "refused"),
  });
  const valid =
    /^[a-z][a-z0-9-]{1,62}$/.test(name) &&
    (type === "oauth2_authorization_code"
      ? /^https:\/\//.test(cardUrl)
      : type === "bearer"
        ? token.length > 0
        : type === "api_key"
          ? token.length > 0 && HEADER_NAME.test(header)
          : true);
  return (
    <Card title="Add a credential">
      <form
        className="grid max-w-3xl gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (!valid) return;
          create.mutate({
            name,
            type,
            ...(type === "oauth2_authorization_code" && { card_url: cardUrl }),
            ...((type === "bearer" || type === "api_key") && { token }),
            ...(type === "api_key" && { header }),
          });
        }}
      >
        <Field label="Name" hint="Lowercase letters, digits and dashes, e.g. atlassian-rovo">
          {(props) => <Input {...props} value={name} onChange={(e) => setName(e.target.value)} placeholder="atlassian-rovo" />}
        </Field>
        <Field label="Type">
          {(props) => (
            <Select {...props} value={type} onChange={(e) => setType(e.target.value as RemoteCredential["type"])}>
              {Object.entries(CREDENTIAL_TYPES).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Select>
          )}
        </Field>
        {type === "oauth2_authorization_code" && (
          <Field
            label="Agent card URL"
            hint="Choregos registers itself at the registration endpoint the card advertises, then you connect it."
          >
            {(props) => (
              <Input
                {...props}
                value={cardUrl}
                onChange={(e) => setCardUrl(e.target.value)}
                placeholder="https://a2a.atlassian.com/.well-known/agent.json"
              />
            )}
          </Field>
        )}
        {type === "api_key" && (
          <Field label="Header" hint="The request header the agent reads the key in, as its card names it: x-sn-apikey for ServiceNow.">
            {(props) => <Input {...props} value={header} onChange={(e) => setHeader(e.target.value)} placeholder="x-sn-apikey" />}
          </Field>
        )}
        {(type === "bearer" || type === "api_key") && (
          <Field label={type === "bearer" ? "Token" : "Key"} hint="Typed once, sealed in the secret store, never shown again.">
            {(props) => (
              <Input {...props} type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} />
            )}
          </Field>
        )}
        <div className="flex items-center gap-3">
          <Button type="submit" tone="primary" disabled={!valid || create.isPending}>
            {create.isPending ? "Adding…" : "Add the credential"}
          </Button>
          {error && <ErrorNote>{error}</ErrorNote>}
        </div>
      </form>
    </Card>
  );
}
