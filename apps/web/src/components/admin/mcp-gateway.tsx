// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { egressFlag, useEgressGuard } from "@/components/egress/egress-provider";
import { GesteConfirme } from "@/components/geste-confirme";
import { Button, Card, ErrorNote, Field, Input, Select } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import {
  AUTH_TYPES,
  directMcpConnectors,
  endpointProblem,
  MODES,
  SERVER_PATTERN,
  type GatewayAuthType,
  type GatewayMode,
  type GatewayRouting,
} from "@/lib/mcp-gateway";
import type { McpGateway, McpGatewayAssertion, McpGatewayPut, McpGatewayTest } from "@/lib/types";

const message = (cause: unknown) => (cause instanceof Error ? cause.message : "refused");

/**
 * The organisation's MCP gateway (ADR 0105): Choregos consumes one, it never is one. The secret is
 * write-only (ADR 0058): the field never holds the stored one, an empty field keeps it, and only
 * `secret_set` comes back. Administrators only: the API refuses the others (403), so the card does
 * not render for them.
 */
export function McpGatewayCard({ org, canEdit }: { org: string; canEdit: boolean }) {
  const state = useQuery({ queryKey: ["mcp-gateway", org], queryFn: () => api.mcpGateway(org), enabled: canEdit });
  if (!canEdit) return null;
  return (
    <Card title="MCP gateway">
      {state.data ? (
        <>
          {/* Keyed by what is stored: a save, or a removal, resets the form to it and empties the secret field. */}
          <GatewayForm
            key={`${state.data.updated_at ?? ""}|${state.data.gateway ? "set" : "none"}`}
            org={org}
            stored={state.data.gateway}
          />
          <AssertionBlock org={org} assertion={state.data.assertion} />
        </>
      ) : state.isError ? (
        <ErrorNote>{message(state.error)}</ErrorNote>
      ) : (
        <p className="text-sm text-ink-muted">Loading…</p>
      )}
    </Card>
  );
}

function GatewayForm({ org, stored }: { org: string; stored: McpGateway | null }) {
  const client = useQueryClient();
  const guard = useEgressGuard();
  const connectors = useQuery({ queryKey: ["org-connectors", org], queryFn: () => api.orgConnectors(org) });
  const [mode, setMode] = useState<GatewayMode>(stored?.mode ?? "available");
  const [endpoint, setEndpoint] = useState(stored?.endpoint ?? "");
  const [routing, setRouting] = useState<GatewayRouting>(stored?.routing ?? "path");
  const [serverHeader, setServerHeader] = useState(stored?.server_header ?? "");
  const [authType, setAuthType] = useState<GatewayAuthType>(stored?.auth.type ?? "bearer");
  const [headerName, setHeaderName] = useState(stored?.auth.header_name ?? "");
  const [tokenUrl, setTokenUrl] = useState(stored?.auth.token_url ?? "");
  const [clientId, setClientId] = useState(stored?.auth.client_id ?? "");
  const [scope, setScope] = useState(stored?.auth.scope ?? "");
  const [resource, setResource] = useState(stored?.auth.resource ?? "");
  const [tokenAudience, setTokenAudience] = useState(stored?.auth.token_audience ?? "");
  const [audience, setAudience] = useState(stored?.audience ?? "");
  const [identityHeader, setIdentityHeader] = useState(stored?.identity_header ?? "");
  const [timeoutS, setTimeoutS] = useState(String(stored?.timeout_s ?? 30));
  const [secret, setSecret] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [server, setServer] = useState("");
  const [testing, setTesting] = useState(false);
  const [test, setTest] = useState<McpGatewayTest | null>(null);
  const [testError, setTestError] = useState<string | null>(null);

  const needsSecret = AUTH_TYPES.find((a) => a.value === authType)?.needsSecret ?? false;
  const secretSet = stored?.secret_set ?? false;
  const endpointError = endpoint.trim() === "" ? null : endpointProblem(endpoint.trim(), routing);
  const timeout = Number(timeoutS);
  const timeoutError = Number.isInteger(timeout) && timeout >= 1 && timeout <= 300 ? null : "A whole number of seconds, 1 to 300.";
  const authComplete =
    (authType !== "header" || headerName.trim() !== "") &&
    (authType !== "oauth_client_credentials" || (tokenUrl.trim() !== "" && clientId.trim() !== "")) &&
    (authType !== "jwt_assertion" || audience.trim() !== "");
  const valid =
    endpoint.trim() !== "" &&
    !endpointError &&
    !timeoutError &&
    (routing !== "header" || serverHeader.trim() !== "") &&
    authComplete &&
    (!needsSecret || secretSet || secret !== "");

  const direct = mode === "required" ? directMcpConnectors(connectors.data ?? []) : [];
  const refresh = () => client.invalidateQueries({ queryKey: ["mcp-gateway", org] });

  async function save() {
    setError(null);
    setSaved(false);
    setBusy(true);
    const opt = (v: string) => (v.trim() === "" ? null : v.trim());
    const body: McpGatewayPut = {
      mode,
      endpoint: endpoint.trim(),
      routing,
      server_header: routing === "header" ? opt(serverHeader) : null,
      auth: {
        type: authType,
        ...(authType === "header" ? { header_name: opt(headerName) } : {}),
        ...(authType === "oauth_client_credentials"
          ? { token_url: opt(tokenUrl), client_id: opt(clientId), scope: opt(scope), resource: opt(resource), token_audience: opt(tokenAudience) }
          : {}),
      },
      audience: opt(audience),
      identity_header: opt(identityHeader),
      timeout_s: timeout,
      // Empty keeps the stored secret: the key is absent, never an empty string.
      ...(secret === "" ? {} : { secret }),
    };
    try {
      await guard((allow) => api.putMcpGateway(org, { ...body, ...egressFlag(allow) }), {
        gesture: "Set the MCP gateway",
        origin: `mcp-gateway:${org}`,
        reason: "The organisation's MCP gateway is reached at this host.",
      });
      setSaved(true);
      setTest(null);
      await refresh();
    } catch (cause) {
      setError(message(cause));
    } finally {
      setBusy(false);
    }
  }

  async function runTest() {
    setTesting(true);
    setTest(null);
    setTestError(null);
    try {
      setTest(await api.testMcpGateway(org, { server: server.trim() }));
    } catch (cause) {
      setTestError(message(cause));
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="space-y-3 text-sm">
      <p className="text-ink-muted">
        One MCP gateway for {org}. Choregos calls the gateway, which fronts the MCP servers; it never is one. The secret is typed once,
        sealed in the secret store and never shown again.
      </p>
      <div className="grid gap-3 sm:grid-cols-2 [&>*]:min-w-0">
        <Field label="Mode" hint={MODES.find((m) => m.value === mode)?.note}>
          {(props) => (
            <Select {...props} value={mode} onChange={(e) => setMode(e.target.value as GatewayMode)}>
              {MODES.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Timeout (seconds)" error={timeoutError}>
          {(props) => <Input {...props} inputMode="numeric" value={timeoutS} onChange={(e) => setTimeoutS(e.target.value)} />}
        </Field>
      </div>
      {mode === "required" && direct.length > 0 && (
        <div className="rounded border border-line border-l-2 border-l-warn p-2" role="alert">
          <p>These direct MCP connectors will not be called while the gateway is required:</p>
          <ul className="list-disc pl-5">
            {direct.map((c) => (
              <li key={c.name}>
                <code>{c.name}</code>
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="grid gap-3 sm:grid-cols-2 [&>*]:min-w-0">
        <Field
          label="Endpoint"
          hint="http or https; {server} is replaced by the server's name. No credentials, query or fragment."
          error={endpointError}
          className="sm:col-span-2"
        >
          {(props) => (
            <Input {...props} value={endpoint} onChange={(e) => setEndpoint(e.target.value)} placeholder="https://gateway.example.com/mcp/{server}" autoComplete="off" />
          )}
        </Field>
        <Field label="Routing" hint="How the server is named to the gateway.">
          {(props) => (
            <Select {...props} value={routing} onChange={(e) => setRouting(e.target.value as GatewayRouting)}>
              <option value="path">In the path</option>
              <option value="header">In a header</option>
            </Select>
          )}
        </Field>
        {routing === "header" && (
          <Field label="Server header" hint="The header that carries the server's name.">
            {(props) => <Input {...props} value={serverHeader} onChange={(e) => setServerHeader(e.target.value)} autoComplete="off" />}
          </Field>
        )}
        <Field label="Auth type">
          {(props) => (
            <Select {...props} value={authType} onChange={(e) => setAuthType(e.target.value as GatewayAuthType)}>
              {AUTH_TYPES.map((a) => (
                <option key={a.value} value={a.value}>
                  {a.label}
                </option>
              ))}
            </Select>
          )}
        </Field>
        {authType === "header" && (
          <Field label="Header name" hint="The header that carries the secret.">
            {(props) => <Input {...props} value={headerName} onChange={(e) => setHeaderName(e.target.value)} autoComplete="off" />}
          </Field>
        )}
        {authType === "oauth_client_credentials" && (
          <>
            <Field label="Token URL">
              {(props) => <Input {...props} value={tokenUrl} onChange={(e) => setTokenUrl(e.target.value)} autoComplete="off" />}
            </Field>
            <Field label="Client ID">
              {(props) => <Input {...props} value={clientId} onChange={(e) => setClientId(e.target.value)} autoComplete="off" />}
            </Field>
            <Field label="Scope">
              {(props) => <Input {...props} value={scope} onChange={(e) => setScope(e.target.value)} autoComplete="off" />}
            </Field>
            <Field label="Resource" hint="RFC 8707 resource indicator.">
              {(props) => <Input {...props} value={resource} onChange={(e) => setResource(e.target.value)} autoComplete="off" />}
            </Field>
            <Field label="Token audience">
              {(props) => <Input {...props} value={tokenAudience} onChange={(e) => setTokenAudience(e.target.value)} autoComplete="off" />}
            </Field>
          </>
        )}
        {needsSecret && (
          <Field
            label="Secret"
            hint={
              secretSet
                ? "Leave empty to keep the stored secret. Typed once, sealed in the secret store, never shown again."
                : "Required the first time. Typed once, sealed in the secret store, never shown again."
            }
          >
            {(props) => (
              <div className="flex min-w-0 items-center gap-2">
                <Input {...props} type="password" autoComplete="new-password" value={secret} onChange={(e) => setSecret(e.target.value)} />
                {secretSet && <span className="text-xs text-ok">set</span>}
              </div>
            )}
          </Field>
        )}
        <Field
          label="Audience"
          hint={authType === "jwt_assertion" ? "Required: the audience of the signed assertion the gateway receives." : "Audience of the signed assertion, if the gateway checks one."}
          error={authType === "jwt_assertion" && audience.trim() === "" ? "Required for a signed assertion." : null}
        >
          {(props) => <Input {...props} value={audience} onChange={(e) => setAudience(e.target.value)} autoComplete="off" />}
        </Field>
        <Field label="Identity header" hint="The header that carries the signed assertion of the caller's identity.">
          {(props) => <Input {...props} value={identityHeader} onChange={(e) => setIdentityHeader(e.target.value)} autoComplete="off" />}
        </Field>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button tone="primary" onClick={() => void save()} disabled={!valid || busy}>
          Save MCP gateway
        </Button>
        {stored && (
          <GesteConfirme
            tonDuBouton="danger"
            question="Remove the MCP gateway and its sealed secret? Connectors that go through it must be changed first, and the secret does not come back."
            confirmer="Remove the gateway"
            action={async () => {
              try {
                await api.deleteMcpGateway(org);
              } catch (cause) {
                const problem = cause instanceof ApiError ? cause.problem : null;
                if (cause instanceof ApiError && cause.status === 409 && problem?.code === "gateway_in_use")
                  throw new Error(`Still used by: ${(problem.connectors ?? []).join(", ") || "some connectors"}`);
                throw cause;
              }
              await refresh();
            }}
          >
            Remove
          </GesteConfirme>
        )}
        {saved && <span className="text-sm text-ok">MCP gateway saved</span>}
      </div>
      {error && <ErrorNote>{error}</ErrorNote>}
      {stored && (
        <div className="flex flex-wrap items-end gap-2">
          <Field label="Server to test" hint="The server's name at the gateway. Tests the stored gateway, not the unsaved form.">
            {(props) => <Input {...props} value={server} onChange={(e) => setServer(e.target.value)} autoComplete="off" placeholder="supplier" />}
          </Field>
          <Button onClick={() => void runTest()} disabled={testing || !SERVER_PATTERN.test(server.trim())}>
            {testing ? "Testing…" : "Test the gateway"}
          </Button>
        </div>
      )}
      {testError && <ErrorNote>{testError}</ErrorNote>}
      {test &&
        (test.ok ? (
          <p className="text-sm text-ok" role="status">
            ok: server {String(test.server_info?.name ?? test.server)}, version {String(test.server_info?.version ?? test.protocol_version ?? "unknown")},{" "}
            {test.tools ?? 0} tools, {test.elapsed_ms} ms
          </p>
        ) : (
          <ErrorNote>
            Test failed after {test.elapsed_ms} ms: {test.error ?? "no reason given"}
          </ErrorNote>
        ))}
    </div>
  );
}

function CopyButton({ text, label }: { text: string; label: string }) {
  return (
    <Button size="sm" aria-label={label} onClick={() => void navigator.clipboard?.writeText(text)}>
      Copy
    </Button>
  );
}

/** What to give the gateway so it can verify the identity Choregos signs, and the keys behind it (ADR 0105). */
function AssertionBlock({ org, assertion }: { org: string; assertion: McpGatewayAssertion }) {
  const client = useQueryClient();
  return (
    <div className="mt-4 space-y-2 border-t border-line pt-3 text-sm">
      <h3 className="font-medium">Signed assertion</h3>
      <p className="text-ink-muted">Give the gateway the issuer and the JWKS URL so it can verify the identity Choregos signs.</p>
      <dl className="space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <dt className="text-ink-muted">Issuer</dt>
          <dd className="min-w-0 break-all">
            <code>{assertion.issuer}</code>
          </dd>
          <CopyButton text={assertion.issuer} label="Copy the issuer" />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <dt className="text-ink-muted">JWKS URL</dt>
          <dd className="min-w-0 break-all">
            <code>{assertion.jwks_url}</code>
          </dd>
          <CopyButton text={assertion.jwks_url} label="Copy the JWKS URL" />
        </div>
      </dl>
      {assertion.keys.length === 0 ? (
        <p className="text-ink-muted">No assertion key yet.</p>
      ) : (
        <ul className="list-disc pl-5">
          {assertion.keys.map((k) => (
            <li key={k.kid}>
              <code className="break-all">{k.kid}</code>, created {k.created_at}
              {k.current ? " (current)" : ""}
            </li>
          ))}
        </ul>
      )}
      <GesteConfirme
        question="Create a new assertion key? It signs from now on; the previous keys stay published until they are retired."
        confirmer="Rotate the key"
        ton="primary"
        tonDuBouton="default"
        action={async () => {
          await api.rotateMcpGatewayAssertionKey(org);
          await client.invalidateQueries({ queryKey: ["mcp-gateway", org] });
        }}
      >
        Rotate key
      </GesteConfirme>
    </div>
  );
}
