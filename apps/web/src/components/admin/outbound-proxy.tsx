// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { GesteConfirme } from "@/components/geste-confirme";
import { Button, Card, ErrorNote, Field, Input, Select } from "@/components/ui";
import { api } from "@/lib/api";
import { endpointProblem, PROXY_KINDS, type ProxyKind } from "@/lib/outbound-proxy";
import type { OutboundProxy, OutboundProxyPut, OutboundProxyTest } from "@/lib/types";

const ALL_KINDS = PROXY_KINDS.map((k) => k.value);

/**
 * The organisation's one outbound proxy for its web search and web reading (ADR 0087). The password
 * is write-only (ADR 0058): the field never holds the stored one, an empty field keeps it, and only
 * `password_set` comes back. Administrators only: the API refuses the others (403), so the card does
 * not render for them.
 */
export function OutboundProxyCard({ org, canEdit }: { org: string; canEdit: boolean }) {
  const state = useQuery({ queryKey: ["outbound-proxy", org], queryFn: () => api.outboundProxy(org), enabled: canEdit });
  if (!canEdit) return null;
  return (
    <Card title="Outbound proxy">
      {state.data ? (
        // Keyed by what is stored: a save, or a removal, resets the form to it and empties the password field.
        <ProxyForm key={`${state.data.updated_at ?? ""}|${state.data.proxy ? "set" : "none"}`} org={org} stored={state.data.proxy} />
      ) : state.isError ? (
        <ErrorNote>{state.error instanceof Error ? state.error.message : "refused"}</ErrorNote>
      ) : (
        <p className="text-sm text-ink-muted">Loading…</p>
      )}
    </Card>
  );
}

function ProxyForm({ org, stored }: { org: string; stored: OutboundProxy | null }) {
  const client = useQueryClient();
  const [enabled, setEnabled] = useState(stored?.enabled ?? true);
  const [endpoint, setEndpoint] = useState(stored?.endpoint ?? "");
  const [scheme, setScheme] = useState<"http" | "https">(stored?.scheme ?? "http");
  const [username, setUsername] = useState(stored?.username ?? "");
  const [password, setPassword] = useState("");
  const [kinds, setKinds] = useState<ProxyKind[]>(stored?.applies_to ?? ALL_KINDS);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);
  const [test, setTest] = useState<OutboundProxyTest | null>(null);
  const [testError, setTestError] = useState<string | null>(null);

  const passwordSet = stored?.password_set ?? false;
  const endpointError = endpoint === "" ? null : endpointProblem(endpoint.trim());
  const valid =
    endpoint.trim() !== "" && !endpointError && username.length <= 512 && (passwordSet || password !== "") && kinds.length > 0;

  const refresh = () => client.invalidateQueries({ queryKey: ["outbound-proxy", org] });

  async function save() {
    setError(null);
    setSaved(false);
    setBusy(true);
    const body: OutboundProxyPut = {
      enabled,
      endpoint: endpoint.trim(),
      scheme,
      username,
      applies_to: ALL_KINDS.filter((k) => kinds.includes(k)),
      // Empty keeps the stored password: the key is absent, never an empty string.
      ...(password === "" ? {} : { password }),
    };
    try {
      await api.putOutboundProxy(org, body);
      setSaved(true);
      setTest(null);
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "refused");
    } finally {
      setBusy(false);
    }
  }

  async function runTest() {
    setTesting(true);
    setTest(null);
    setTestError(null);
    try {
      setTest(await api.testOutboundProxy(org));
    } catch (cause) {
      setTestError(cause instanceof Error ? cause.message : "refused");
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="space-y-3 text-sm">
      <p className="text-ink-muted">
        One proxy for the web search and web reading of {org}. When it is enabled for a kind of traffic, that traffic goes through it or
        not at all: an unreachable proxy is an error, never a direct call. The target is still checked against the network access
        above. Agent runs are not concerned.
      </p>
      <label className="flex items-center gap-2">
        <input type="checkbox" role="switch" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        <span>Enabled</span>
      </label>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Endpoint" hint="host:port, without a scheme or credentials." error={endpointError}>
          {(props) => (
            <Input {...props} value={endpoint} onChange={(e) => setEndpoint(e.target.value)} placeholder="gate.example.com:7000" autoComplete="off" />
          )}
        </Field>
        <Field label="Scheme" hint="The protocol spoken to the proxy; an https target goes through CONNECT.">
          {(props) => (
            <Select {...props} value={scheme} onChange={(e) => setScheme(e.target.value as "http" | "https")}>
              <option value="http">http</option>
              <option value="https">https</option>
            </Select>
          )}
        </Field>
        <Field
          label="Username"
          hint="Provider options such as a country or a sticky session go in the username, as your provider documents them (for example user-…-country-fr-session-…). Choregos does not build them."
          error={username.length > 512 ? "At most 512 characters." : null}
        >
          {(props) => <Input {...props} value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="off" />}
        </Field>
        <Field
          label="Password"
          hint={
            passwordSet
              ? "Leave empty to keep the stored password. Typed once, sealed in the secret store, never shown again."
              : "Required the first time. Typed once, sealed in the secret store, never shown again."
          }
        >
          {(props) => (
            <div className="flex items-center gap-2">
              <Input {...props} type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
              {passwordSet && <span className="text-xs text-ok">set</span>}
            </div>
          )}
        </Field>
      </div>
      <fieldset className="space-y-1.5">
        <legend className="text-xs font-medium text-ink-muted">Applies to</legend>
        {PROXY_KINDS.map(({ value, label, note }) => (
          <label key={value} className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={kinds.includes(value)}
              onChange={(e) => setKinds(e.target.checked ? [...kinds, value] : kinds.filter((k) => k !== value))}
            />
            <span>
              {label}
              {note && <span className="text-ink-muted"> — {note}</span>}
            </span>
          </label>
        ))}
        {kinds.length === 0 && <p className="text-xs text-danger">Choose at least one kind of traffic.</p>}
      </fieldset>
      <div className="flex flex-wrap items-center gap-2">
        <Button tone="primary" onClick={() => void save()} disabled={!valid || busy}>
          Save outbound proxy
        </Button>
        {stored && (
          <Button onClick={() => void runTest()} disabled={testing} title="Tests the stored proxy, not the unsaved form">
            {testing ? "Testing…" : "Test the proxy"}
          </Button>
        )}
        {stored && (
          <GesteConfirme
            tonDuBouton="danger"
            question="Remove the outbound proxy and its sealed password? Web search and web reading go direct again, and the password does not come back."
            confirmer="Remove the proxy"
            action={async () => {
              await api.deleteOutboundProxy(org);
              await refresh();
            }}
          >
            Remove
          </GesteConfirme>
        )}
        {saved && <span className="text-sm text-ok">Outbound proxy saved</span>}
      </div>
      {error && <ErrorNote>{error}</ErrorNote>}
      {testError && <ErrorNote>{testError}</ErrorNote>}
      {test &&
        (test.ok ? (
          <p className="text-sm text-ok" role="status">
            Exit IP <code>{test.exit_ip ?? "unknown"}</code>, country {test.country ?? "unknown"}, {test.elapsed_ms} ms
          </p>
        ) : (
          <ErrorNote>
            Test failed after {test.elapsed_ms} ms: {test.error ?? "no reason given"}
          </ErrorNote>
        ))}
    </div>
  );
}
