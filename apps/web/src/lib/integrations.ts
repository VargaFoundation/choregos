// SPDX-License-Identifier: Apache-2.0
/**
 * Les extraits de la page Integrations (ADR 0030), en fonctions pures : ce qu'on colle dans un
 * client MCP se teste sans navigateur.
 *
 * Deux règles, chacune tenue par un test : un extrait MCP ne porte jamais un jeton `*` (la page n'en
 * frappe que de portée `mcp:*`), et `--allow-http` n'apparaît que pour une URL en `http:`.
 *
 * Quand la porte accepte les jetons de l'IdP et que le déploiement nomme le client que l'IdP a
 * enregistré (`oauth.clients`), l'extrait le dit : on se connecte par l'authentification unique,
 * sans jeton à coller — et un secret de client n'apparaît jamais ici.
 */

import type { Integrations } from "@choregos/contracts/api";

export type ClientId =
  | "claude-code"
  | "claude-desktop"
  | "claude-ai"
  | "cursor"
  | "vscode"
  | "chatgpt"
  | "other"
  | "cli"
  | "rest";

export interface ClientInfo {
  id: ClientId;
  label: string;
  /** D'où le client appelle : de la machine de la personne, ou du réseau de son éditeur. */
  calls_from: "your machine" | "the vendor's cloud";
  auth: "MCP token" | "OAuth" | "full-access token";
  /** Ce qu'il faut pour qu'il marche ici et maintenant. */
  status: "ready" | "needs a public HTTPS address and OAuth" | "needs a public HTTPS address";
}

export type OAuth = Integrations["oauth"];

export interface ClientOAuth {
  client_id: string;
  callback_port?: number | null;
}

export const CLIENTS: ReadonlyArray<ClientInfo> = [
  { id: "claude-code", label: "Claude Code", calls_from: "your machine", auth: "MCP token", status: "ready" },
  { id: "claude-desktop", label: "Claude Desktop", calls_from: "your machine", auth: "MCP token", status: "ready" },
  {
    id: "claude-ai",
    label: "claude.ai",
    calls_from: "the vendor's cloud",
    auth: "OAuth",
    status: "needs a public HTTPS address and OAuth",
  },
  { id: "cursor", label: "Cursor", calls_from: "your machine", auth: "MCP token", status: "ready" },
  { id: "vscode", label: "VS Code", calls_from: "your machine", auth: "MCP token", status: "ready" },
  {
    id: "chatgpt",
    label: "ChatGPT",
    calls_from: "the vendor's cloud",
    auth: "OAuth",
    status: "needs a public HTTPS address and OAuth",
  },
  { id: "other", label: "other MCP client", calls_from: "your machine", auth: "MCP token", status: "ready" },
  { id: "cli", label: "CLI", calls_from: "your machine", auth: "full-access token", status: "ready" },
  { id: "rest", label: "REST API", calls_from: "your machine", auth: "full-access token", status: "ready" },
];

export const PLACEHOLDER = "<token>";

export function estClient(valeur: string): valeur is ClientId {
  return CLIENTS.some((client) => client.id === valeur);
}

/** Le client que l'IdP a enregistré pour ce client de la page — `null` quand la porte n'accepte pas OAuth. */
export function clientOAuth(client: ClientId, oauth?: OAuth | null): ClientOAuth | null {
  if (!oauth?.enabled) return null;
  return oauth.clients?.[client] ?? null;
}

/**
 * Ce qu'un client demande ICI : un client OAuth enregistré change la façon de se connecter, et
 * claude.ai n'est prêt qu'avec une adresse publique en HTTPS — ce que la page ne peut pas deviner,
 * sinon par le schéma de l'URL.
 */
export function etatDuClient(info: ClientInfo, url: string, oauth?: OAuth | null): ClientInfo {
  if (!clientOAuth(info.id, oauth)) return info;
  if (info.calls_from === "the vendor's cloud") {
    return { ...info, auth: "OAuth", status: url.startsWith("https:") ? "ready" : "needs a public HTTPS address" };
  }
  return { ...info, auth: "OAuth", status: "ready" };
}

/** L'URL de la porte d'un projet : `/mcp/projects/org:slug`. Sans projet, la porte de tous. */
export function urlDeLaPorte(base: string, projet?: string | null): string {
  const racine = base.replace(/\/+$/, "");
  return projet ? `${racine}/projects/${projet}` : racine;
}

/** L'origine de la plateforme, déduite de l'URL de la porte (`…/mcp`). */
export function origineDe(urlMcp: string): string {
  return urlMcp.replace(/\/mcp(\/.*)?$/, "");
}

export interface Extrait {
  /** Où le coller : un fichier, un terminal. */
  where: string;
  language: "bash" | "json" | "text";
  code: string;
}

/**
 * L'extrait d'un client, ou `null` quand il n'y en a pas (claude.ai et ChatGPT : il faut une adresse
 * publique et OAuth). Avec `oauth`, le client que l'IdP a enregistré remplace le jeton.
 */
export function extrait(
  client: ClientId,
  url: string,
  jeton: string = PLACEHOLDER,
  oauth?: OAuth | null,
): Extrait | null {
  const http = url.startsWith("http:");
  const enregistre = clientOAuth(client, oauth);
  switch (client) {
    case "claude-code":
      if (enregistre) {
        const port = enregistre.callback_port ? ` --callback-port ${enregistre.callback_port}` : "";
        return {
          where: "a terminal — then /mcp in Claude Code, choose choregos and Authenticate: your browser signs you in",
          language: "bash",
          code: `claude mcp add --transport http --client-id ${enregistre.client_id}${port} choregos ${url}`,
        };
      }
      return {
        where: "a terminal",
        language: "bash",
        code: [
          `export CHOREGOS_TOKEN=${jeton}`,
          `claude mcp add --transport http choregos ${url} --header "Authorization: Bearer $CHOREGOS_TOKEN"`,
        ].join("\n"),
      };
    case "claude-desktop":
      return {
        where: "claude_desktop_config.json",
        language: "json",
        code: JSON.stringify(
          {
            mcpServers: {
              choregos: {
                command: "npx",
                args: ["-y", "mcp-remote", url, "--header", "Authorization:${AUTH}", ...(http ? ["--allow-http"] : [])],
                env: { AUTH: `Bearer ${jeton}` },
              },
            },
          },
          null,
          2,
        ),
      };
    case "cursor":
      return {
        where: "~/.cursor/mcp.json — then export CHOREGOS_TOKEN in your shell",
        language: "json",
        code: JSON.stringify(
          { mcpServers: { choregos: { url, headers: { Authorization: "Bearer ${env:CHOREGOS_TOKEN}" } } } },
          null,
          2,
        ),
      };
    case "vscode":
      return {
        where: ".vscode/mcp.json — VS Code asks for the token once and keeps it in its secret store",
        language: "json",
        code: JSON.stringify(
          {
            inputs: [{ type: "promptString", id: "choregos-token", description: "Choregos MCP token", password: true }],
            servers: { choregos: { type: "http", url, headers: { Authorization: "Bearer ${input:choregos-token}" } } },
          },
          null,
          2,
        ),
      };
    case "other":
      return {
        where: "your client's MCP settings",
        language: "text",
        code: [`URL:        ${url}`, "Transport:  Streamable HTTP", `Header:     Authorization: Bearer ${jeton}`].join(
          "\n",
        ),
      };
    case "cli":
      return {
        where: "a terminal — the CLI needs a full-access token, created in administration",
        language: "bash",
        code: `choregos login --api-url ${origineDe(url)} --token <full-access token>`,
      };
    case "rest":
      return {
        where: "a terminal — the REST API needs a full-access token, created in administration",
        language: "bash",
        code: `curl -H "Authorization: Bearer <full-access token>" ${origineDe(url)}/api/v1/me`,
      };
    case "claude-ai":
      // claude.ai appelle depuis le réseau d'Anthropic : sans adresse publique en HTTPS, rien à coller.
      if (enregistre && !http) {
        return {
          where: "claude.ai — Settings, Connectors, Add custom connector, then Advanced settings",
          language: "text",
          code: [
            "Name:                  Choregos",
            `Remote MCP server URL: ${url}`,
            `OAuth Client ID:       ${enregistre.client_id}`,
            "OAuth Client Secret:   ask your administrator — it is never shown on this page",
          ].join("\n"),
        };
      }
      return null;
    case "chatgpt":
      return null;
  }
}
