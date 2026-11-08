// SPDX-License-Identifier: Apache-2.0
"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Alert, Button, Card, Code, ErrorNote, Tabs, Select, tabClasses } from "@/components/ui";
import { api, qualify } from "@/lib/api";
import { shortDate } from "@/lib/format";
import {
  CLIENTS,
  type ClientId,
  clientOAuth,
  clientState,
  extrait,
  PLACEHOLDER,
  gateUrl,
} from "@/lib/integrations";
import type { ApiTokenCreated } from "@/lib/types";

const DUREES = [7, 30, 90] as const;

/**
 * The Integrations page (ADR 0030): how to plug IN your agent (Claude Code, Claude Desktop,
 * Cursor, VS Code…) into the MCP door, with a token minted for that client, and the finding that it
 * did connect.
 *
 * In a project (`projet` set), the door is the project's and the token is bound to it.
 *
 * When the door accepts the IdP's tokens and the deployment names the registered client,
 * single sign-on comes first: nothing to paste, nothing to expire.
 * The token is still offered, one click further.
 */
export function IntegrationsPanel({ client, projet, base }: { client: ClientId; projet?: string; base: string }) {
  const scope = projet ? qualify(projet) : undefined;
  const infos = useQuery({ queryKey: ["integrations"], queryFn: () => api.integrations(), staleTime: Infinity });
  const url = infos.data ? gateUrl(infos.data.mcp_url, scope) : "…";
  const [ecriture, setEcriture] = useState(false);
  const [duree, setDuree] = useState<(typeof DUREES)[number]>(30);
  const [frappe, setFrappe] = useState<ApiTokenCreated | null>(null);
  const [erreur, setErreur] = useState<string | null>(null);
  const [parJeton, setParJeton] = useState(false);
  const oauth = infos.data?.oauth;
  const generique = CLIENTS.find((c) => c.id === client) ?? CLIENTS[0]!;
  // Single sign-on first, when the IdP has a client for this one; the token on request.
  const sso = clientOAuth(client, oauth) !== null && !parJeton;
  const info = sso ? clientState(generique, url, oauth) : generique;
  const code = extrait(client, url, frappe?.token ?? PLACEHOLDER, sso ? oauth : null);
  const withMcpToken = info.auth === "MCP token";
  const alsoByToken = clientOAuth(client, oauth) !== null && generique.auth === "MCP token";

  async function frapper() {
    setErreur(null);
    try {
      const cree = await api.createToken({
        name: info.label,
        scopes: [ecriture ? "mcp:write" : "mcp:read"],
        project: scope ?? null,
        expires_in_days: duree,
      });
      setFrappe(cree);
    } catch (cause) {
      setErreur(cause instanceof Error ? cause.message : "token refused");
    }
  }

  return (
    <div className="space-y-6">
      <Tabs aria-label="Clients">
        {CLIENTS.map((c) => {
          const href = `${base}/${c.id}`;
          const active = c.id === client;
          return (
            <Link key={c.id} href={href} aria-current={active ? "page" : undefined} className={tabClasses(active)}>
              {c.label}
            </Link>
          );
        })}
      </Tabs>

      {erreur && <ErrorNote>{erreur}</ErrorNote>}

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title={`Connect ${info.label}`} className="lg:col-span-2">
          <p className="text-sm text-ink-muted">
            {scope ? (
              <>
                The door of project <Code>{scope}</Code>: tools need no project argument, and the token is bound to
                this project.
              </>
            ) : (
              <>The door serves every project you can read; tools take a project argument.</>
            )}
          </p>
          <p className="mt-2 break-all text-sm">
            MCP URL <Code data-testid="mcp-url">{url}</Code>
          </p>

          {code ? (
            <div className="mt-4 space-y-2">
              <p className="text-xs text-ink-muted">Paste into {code.where}</p>
              <Extrait code={code.code} />
            </div>
          ) : clientOAuth(client, oauth) ? (
            <Alert tone="warn" title="Not reachable from here yet" className="mt-4">
              {info.label} calls MCP servers from its vendor&apos;s network: the door accepts its sign-in, but this
              platform has no public HTTPS address for <Code>/mcp</Code>. A platform behind a VPN stays out of its
              reach: use Claude Code meanwhile.
            </Alert>
          ) : (
            <Alert tone="warn" title="Not reachable from here yet" className="mt-4">
              {info.label} calls MCP servers from its vendor&apos;s network and signs in with OAuth. It needs a public
              HTTPS address for <Code>/mcp</Code> and OAuth on the door, which this platform does not offer yet. A platform behind a
              VPN stays out of its reach: use Claude Code or Claude Desktop meanwhile.
            </Alert>
          )}
          {alsoByToken && (
            <p className="mt-3 text-xs text-ink-muted">
              {sso ? (
                <>
                  you sign in with your single sign-on: nothing to paste, your session decides.{" "}
                  <button type="button" className="underline" onClick={() => setParJeton(true)}>
                    Use an MCP token instead
                  </button>
                </>
              ) : (
                <button type="button" className="underline" onClick={() => setParJeton(false)}>
                  Sign in with single sign-on instead
                </button>
              )}
            </p>
          )}
        </Card>

        {withMcpToken && (
          <Card title="A token for this client">
            {frappe ? (
              <div className="space-y-3 text-sm">
                <p>
                  Token <strong>{frappe.name}</strong> ({(frappe.scopes ?? []).join(", ")}
                  {frappe.project ? `, ${frappe.project}` : ""}) — shown once, already in the snippet.
                </p>
                <TokenState id={frappe.id} />
              </div>
            ) : (
              <div className="space-y-3 text-sm">
                <label className="flex items-center gap-2">
                  <input type="checkbox" checked={ecriture} onChange={(e) => setEcriture(e.target.checked)} />
                  <span>Can open work items (mcp:write)</span>
                </label>
                <label className="flex items-center gap-2">
                  <span className="text-ink-muted">Expires in</span>
                  <Select
                    aria-label="Expires in"
                    value={duree}
                    onChange={(e) => setDuree(Number(e.target.value) as (typeof DUREES)[number])}
                    className="w-auto"
                  >
                    {DUREES.map((days) => (
                      <option key={days} value={days}>
                        {days} days
                      </option>
                    ))}
                  </Select>
                </label>
                <Button tone="accent" onClick={() => void frapper()}>
                  Create an access token
                </Button>
                <p className="text-xs text-ink-muted">
                  It opens the MCP door only: the REST API refuses it, and it can never decide. Connected this way,{" "}
                  {info.label} acts as you. To give it a name, limits and an audit trail of its own, connect it from{" "}
                  <Link href="/agents">Agents</Link> instead: it becomes an external agent. Every token is listed, and
                  revoked, in <Link href="/account#tokens">My account › Access tokens</Link>.
                </p>
              </div>
            )}
          </Card>
        )}
      </div>

      <Card title="Which client reaches what">
        <div className="overflow-x-auto">
          <table aria-label="Capabilities">
            <thead>
              <tr>
                <th>Client</th>
                <th>Calls from</th>
                <th>Signs in with</th>
                <th>Here and now</th>
              </tr>
            </thead>
            <tbody>
              {CLIENTS.map((c) => clientState(c, url, oauth)).map((c) => (
                <tr key={c.id}>
                  <td>{c.label}</td>
                  <td className="text-xs">{c.calls_from}</td>
                  <td className="text-xs">{c.auth}</td>
                  <td className="text-xs">{c.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card title="When it does not connect">
        <ul className="space-y-2 text-sm">
          <li>
            <strong>401</strong> — the token is missing, revoked or expired. <strong>403 insufficient_scope</strong> — a
            full-access token: create an MCP one here.
          </li>
          <li>
            <strong>The console&apos;s 404 page</strong> — the ingress does not route <Code>/mcp</Code> to the API
            (chart 0.14 or later).
          </li>
          <li>
            <strong>Plain HTTP</strong> — Claude Desktop&apos;s bridge needs <Code>--allow-http</Code>; claude.ai and
            ChatGPT refuse it.
          </li>
          <li>
            <strong>An internal host</strong> — claude.ai and ChatGPT call from their vendor&apos;s cloud (Anthropic:
            160.79.104.0/21) and cannot reach a host behind your VPN; Claude Code can.
          </li>
          <li>
            <strong>A tool is missing</strong> — a read-only token, a role without the right, or a project whose tracker
            is external.
          </li>
        </ul>
      </Card>
    </div>
  );
}

/** A block to copy. Without `navigator.clipboard` (a page over HTTP), the text stays selectable. */
function Extrait({ code }: { code: string }) {
  const [copie, setCopie] = useState(false);
  const pressePapier = typeof navigator !== "undefined" ? navigator.clipboard : undefined;
  return (
    <div className="relative">
      {/* Lines wrap rather than scroll: a block that scrolls would have to be
              reachable by keyboard (axe), and the copy keeps the exact text. */}
      <pre className="whitespace-pre-wrap break-all bg-surface-sunken p-3 text-xs" data-testid="snippet">
        <code>{code}</code>
      </pre>
      {pressePapier && (
        <div className="absolute right-2 top-2">
          <Button
            size="sm"
            onClick={() => {
              void pressePapier.writeText(code).then(() => setCopie(true));
            }}
          >
            {copie ? "Copied" : "Copy"}
          </Button>
        </div>
      )}
    </div>
  );
}

/** "Connected": the token's last use, re-read every three seconds until there is one. */
function TokenState({ id }: { id: string }) {
  const tokens = useQuery({
    queryKey: ["tokens"],
    queryFn: () => api.myTokens(),
    refetchInterval: (query) => {
      const token = query.state.data?.find((t) => t.id === id);
      return token?.last_used_at ? false : 3000;
    },
  });
  const usedToken = tokens.data?.find((t) => t.id === id);
  if (!usedToken?.last_used_at) {
    return (
      <p className="text-xs text-ink-muted" data-testid="connection-status">
        Waiting for the first call from your client…
      </p>
    );
  }
  return (
    <p className="text-xs" data-testid="connection-status">
      Connected — last call {shortDate(usedToken.last_used_at)}
      {usedToken.last_client ? ` from ${usedToken.last_client}` : ""}
    </p>
  );
}
