// SPDX-License-Identifier: Apache-2.0
/**
 * The snippets of the Integrations page (ADR 0030), as pure functions: what one pastes into an MCP
 * client is tested without a browser.
 *
 * Two rules, each held by a test: an MCP snippet never carries a `*` token (the page only mints
 * tokens scoped `mcp:*`), and `--allow-http` appears only for an `http:` URL.
 *
 * When the gate accepts the IdP's tokens and the deployment names the client the IdP registered
 * (`oauth.clients`), the snippet says so: one signs in through single sign-on, with no token to
 * paste, and a client secret never appears here.
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
  /** Where the client calls from: the person's machine, or its vendor's network. */
  calls_from: "your machine" | "the vendor's cloud";
  auth: "MCP token" | "OAuth" | "full-access token";
  /** What it takes for it to work here and now. */
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
  { id: "other", label: "Other MCP client", calls_from: "your machine", auth: "MCP token", status: "ready" },
  { id: "cli", label: "CLI", calls_from: "your machine", auth: "full-access token", status: "ready" },
  { id: "rest", label: "REST API", calls_from: "your machine", auth: "full-access token", status: "ready" },
];

export const PLACEHOLDER = "<token>";

export function estClient(value: string): value is ClientId {
  return CLIENTS.some((client) => client.id === value);
}

/** The client the IdP registered for this client of the page, or `null` when the gate does not accept OAuth. */
export function clientOAuth(client: ClientId, oauth?: OAuth | null): ClientOAuth | null {
  if (!oauth?.enabled) return null;
  return oauth.clients?.[client] ?? null;
}

/**
 * What a client needs HERE: a registered OAuth client changes the way to connect, and claude.ai is
 * only ready with a public HTTPS address, which the page cannot guess except from the URL's scheme.
 */
export function clientState(info: ClientInfo, url: string, oauth?: OAuth | null): ClientInfo {
  if (!clientOAuth(info.id, oauth)) return info;
  if (info.calls_from === "the vendor's cloud") {
    return { ...info, auth: "OAuth", status: url.startsWith("https:") ? "ready" : "needs a public HTTPS address" };
  }
  return { ...info, auth: "OAuth", status: "ready" };
}

/** The URL of a project's gate: `/mcp/projects/org:slug`. Without a project, the gate of all. */
export function gateUrl(base: string, project?: string | null): string {
  const root = base.replace(/\/+$/, "");
  return project ? `${root}/projects/${project}` : root;
}

/** The platform's origin, derived from the gate's URL (`.../mcp`). */
export function origineDe(urlMcp: string): string {
  return urlMcp.replace(/\/mcp(\/.*)?$/, "");
}

/** The organisation's A2A agent card (ADR 0078): beside `/mcp`, at the platform's origin. */
export function a2aCardUrl(urlMcp: string, org: string): string {
  return `${origineDe(urlMcp)}/a2a/orgs/${encodeURIComponent(org)}/.well-known/agent-card.json`;
}

/** `a2a.max_file_bytes` when a project does not set it, and the most it may set (`project.schema.json`). */
export const A2A_MAX_FILE_BYTES_DEFAULT = 10 * 1024 * 1024;
export const A2A_MAX_FILE_BYTES_CEILING = 50 * 1024 * 1024;

/** What one A2A message may carry in file parts for this project: each file, and all of them together. */
export function a2aFileLimit(config: { a2a?: { max_file_bytes?: number } } | null | undefined): number {
  return config?.a2a?.max_file_bytes ?? A2A_MAX_FILE_BYTES_DEFAULT;
}

/** `10485760` as `10 MiB`; a limit that is not a whole number of MiB keeps one decimal, or reads in KiB. */
export function formatMiB(bytes: number): string {
  const mib = bytes / (1024 * 1024);
  if (Number.isInteger(mib)) return `${mib} MiB`;
  if (mib >= 0.1) return `${mib.toFixed(1)} MiB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KiB`;
}

export interface Extrait {
  /** Where to paste it: a file, a terminal. */
  where: string;
  language: "bash" | "json" | "text";
  code: string;
}

/**
 * A client's snippet, or `null` when there is none (claude.ai and ChatGPT need a public address and
 * OAuth). With `oauth`, the client the IdP registered replaces the token.
 */
export function extrait(
  client: ClientId,
  url: string,
  token: string = PLACEHOLDER,
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
          `export CHOREGOS_TOKEN=${token}`,
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
                env: { AUTH: `Bearer ${token}` },
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
        code: [`URL:        ${url}`, "Transport:  Streamable HTTP", `Header:     Authorization: Bearer ${token}`].join(
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
      // claude.ai calls from Anthropic's network: without a public HTTPS address, there is nothing to paste.
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
