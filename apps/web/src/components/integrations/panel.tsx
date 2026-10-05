// SPDX-License-Identifier: Apache-2.0
"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Alert, Code, TabList, tabClasses } from "@varga/design-system";
import { useState } from "react";
import { Button, Card, ErrorNote } from "@/components/ui";
import { api, qualify } from "@/lib/api";
import { shortDate } from "@/lib/format";
import { CLIENTS, type ClientId, extrait, PLACEHOLDER, urlDeLaPorte } from "@/lib/integrations";
import type { ApiTokenCreated } from "@/lib/types";

const DUREES = [7, 30, 90] as const;

/**
 * La page Integrations (ADR 0030) : comment brancher SON agent — Claude Code, Claude Desktop,
 * Cursor, VS Code… — sur la porte MCP, avec un jeton frappé pour ce client, et le constat qu'il
 * s'est bien connecté.
 *
 * Dans un projet (`projet` renseigné), la porte est celle du projet et le jeton y est lié.
 */
export function IntegrationsPanel({ client, projet, base }: { client: ClientId; projet?: string; base: string }) {
  const portee = projet ? qualify(projet) : undefined;
  const infos = useQuery({ queryKey: ["integrations"], queryFn: () => api.integrations(), staleTime: Infinity });
  const url = infos.data ? urlDeLaPorte(infos.data.mcp_url, portee) : "…";
  const [ecriture, setEcriture] = useState(false);
  const [duree, setDuree] = useState<(typeof DUREES)[number]>(30);
  const [frappe, setFrappe] = useState<ApiTokenCreated | null>(null);
  const [erreur, setErreur] = useState<string | null>(null);
  const info = CLIENTS.find((c) => c.id === client) ?? CLIENTS[0]!;
  const code = extrait(client, url, frappe?.token ?? PLACEHOLDER);
  const avecJetonMcp = info.auth === "MCP token";

  async function frapper() {
    setErreur(null);
    try {
      const cree = await api.createToken({
        name: info.label,
        scopes: [ecriture ? "mcp:write" : "mcp:read"],
        project: portee ?? null,
        expires_in_days: duree,
      });
      setFrappe(cree);
    } catch (cause) {
      setErreur(cause instanceof Error ? cause.message : "token refused");
    }
  }

  return (
    <div className="space-y-6">
      <TabList aria-label="clients">
        {CLIENTS.map((c) => {
          const href = `${base}/${c.id}`;
          const active = c.id === client;
          return (
            <Link key={c.id} href={href} aria-current={active ? "page" : undefined} className={tabClasses(active)}>
              {c.label}
            </Link>
          );
        })}
      </TabList>

      {erreur && <ErrorNote>{erreur}</ErrorNote>}

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title={`connect ${info.label}`} className="lg:col-span-2">
          <p className="text-sm text-ink-muted">
            {portee ? (
              <>
                the door of project <Code>{portee}</Code>: tools need no project argument, and the token is bound to
                this project.
              </>
            ) : (
              <>the door serves every project you can read; tools take a project argument.</>
            )}
          </p>
          <p className="mt-2 text-sm">
            MCP URL <Code data-testid="mcp-url">{url}</Code>
          </p>

          {code ? (
            <div className="mt-4 space-y-2">
              <p className="text-xs text-ink-muted">paste into {code.where}</p>
              <Extrait code={code.code} />
            </div>
          ) : (
            <Alert tone="warn" title="not reachable from here yet" className="mt-4">
              {info.label} calls MCP servers from its vendor&apos;s network and signs in with OAuth. It needs a public
              HTTPS address for <Code>/mcp</Code> and OAuth on the door — the next step of ADR 0030. A platform behind
              a VPN stays out of its reach: use Claude Code or Claude Desktop meanwhile.
            </Alert>
          )}
        </Card>

        {avecJetonMcp && (
          <Card title="a token for this client">
            {frappe ? (
              <div className="space-y-3 text-sm">
                <p>
                  token <strong>{frappe.name}</strong> ({(frappe.scopes ?? []).join(", ")}
                  {frappe.project ? `, ${frappe.project}` : ""}) — shown once, already in the snippet.
                </p>
                <Etat id={frappe.id} />
              </div>
            ) : (
              <div className="space-y-3 text-sm">
                <label className="flex items-center gap-2">
                  <input type="checkbox" checked={ecriture} onChange={(e) => setEcriture(e.target.checked)} />
                  <span>can open work items (mcp:write)</span>
                </label>
                <label className="flex items-center gap-2">
                  <span className="text-ink-muted">expires in</span>
                  <select
                    aria-label="expires in"
                    value={duree}
                    onChange={(e) => setDuree(Number(e.target.value) as (typeof DUREES)[number])}
                    className="rounded border border-line bg-surface px-2 py-1"
                  >
                    {DUREES.map((jours) => (
                      <option key={jours} value={jours}>
                        {jours} days
                      </option>
                    ))}
                  </select>
                </label>
                <Button tone="accent" onClick={() => void frapper()}>
                  create a token for {info.label}
                </Button>
                <p className="text-xs text-ink-muted">
                  an MCP token opens the MCP door only: the REST API refuses it, and it can never decide.
                </p>
              </div>
            )}
          </Card>
        )}
      </div>

      <Card title="which client reaches what">
        <table aria-label="capabilities">
          <thead>
            <tr>
              <th>client</th>
              <th>calls from</th>
              <th>signs in with</th>
              <th>here and now</th>
            </tr>
          </thead>
          <tbody>
            {CLIENTS.map((c) => (
              <tr key={c.id}>
                <td>{c.label}</td>
                <td className="text-xs">{c.calls_from}</td>
                <td className="text-xs">{c.auth}</td>
                <td className="text-xs">{c.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <Card title="when it does not connect">
        <ul className="space-y-2 text-sm">
          <li>
            <strong>401</strong> — the token is missing, revoked or expired. <strong>403 insufficient_scope</strong> — a
            full-access token: create an MCP one here.
          </li>
          <li>
            <strong>the console&apos;s 404 page</strong> — the ingress does not route <Code>/mcp</Code> to the API
            (chart 0.14 or later).
          </li>
          <li>
            <strong>plain HTTP</strong> — Claude Desktop&apos;s bridge needs <Code>--allow-http</Code>; claude.ai and
            ChatGPT refuse it.
          </li>
          <li>
            <strong>an internal host</strong> — claude.ai and ChatGPT call from their vendor&apos;s cloud
            (Anthropic: 160.79.104.0/21) and cannot reach a host behind your VPN; Claude Code can.
          </li>
          <li>
            <strong>a tool is missing</strong> — a read-only token, a role without the right, or a project whose
            tracker is external.
          </li>
        </ul>
      </Card>
    </div>
  );
}

/** Un bloc à copier. Sans `navigator.clipboard` (une page en HTTP), le texte reste sélectionnable. */
function Extrait({ code }: { code: string }) {
  const [copie, setCopie] = useState(false);
  const pressePapier = typeof navigator !== "undefined" ? navigator.clipboard : undefined;
  return (
    <div className="relative">
      {/* Les lignes reviennent à la ligne plutôt que de défiler : un bloc qui défile devrait
          s'atteindre au clavier (axe), et la copie garde le texte exact. */}
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
            {copie ? "copied" : "copy"}
          </Button>
        </div>
      )}
    </div>
  );
}

/** « Connecté » : le dernier usage du jeton, relu toutes les trois secondes tant qu'il n'y en a pas. */
function Etat({ id }: { id: string }) {
  const jetons = useQuery({
    queryKey: ["tokens"],
    queryFn: () => api.myTokens(),
    refetchInterval: (requete) => {
      const jeton = requete.state.data?.find((t) => t.id === id);
      return jeton?.last_used_at ? false : 3000;
    },
  });
  const jeton = jetons.data?.find((t) => t.id === id);
  if (!jeton?.last_used_at) {
    return (
      <p className="text-xs text-ink-muted" data-testid="connection-status">
        waiting for the first call from your client…
      </p>
    );
  }
  return (
    <p className="text-xs" data-testid="connection-status">
      connected — last call {shortDate(jeton.last_used_at)}
      {jeton.last_client ? ` from ${jeton.last_client}` : ""}
    </p>
  );
}
