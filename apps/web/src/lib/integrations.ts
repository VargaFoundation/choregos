// SPDX-License-Identifier: Apache-2.0
/**
 * Les extraits de la page Integrations (ADR 0030), en fonctions pures : ce qu'on colle dans un
 * client MCP se teste sans navigateur.
 *
 * Deux règles, chacune tenue par un test : un extrait MCP ne porte jamais un jeton `*` (la page n'en
 * frappe que de portée `mcp:*`), et `--allow-http` n'apparaît que pour une URL en `http:`.
 */

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
  status: "ready" | "needs a public HTTPS address and OAuth";
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
  { id: "other", label: "Other MCP client", calls_from: "your machine", auth: "MCP token", status: "ready" },
  { id: "cli", label: "CLI", calls_from: "your machine", auth: "full-access token", status: "ready" },
  { id: "rest", label: "REST API", calls_from: "your machine", auth: "full-access token", status: "ready" },
];

export const PLACEHOLDER = "<token>";

export function estClient(valeur: string): valeur is ClientId {
  return CLIENTS.some((client) => client.id === valeur);
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
 * publique et OAuth, que la porte n'a pas encore).
 */
export function extrait(client: ClientId, url: string, jeton: string = PLACEHOLDER): Extrait | null {
  const http = url.startsWith("http:");
  switch (client) {
    case "claude-code":
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
    case "chatgpt":
      return null;
  }
}
