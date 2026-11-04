import { describe, expect, it } from "vitest";
import { CLIENTS, clientOAuth, clientState, extrait, type OAuth, origineDe, PLACEHOLDER, gateUrl } from "@/lib/integrations";

const URL_HTTPS = "https://choregos.example/mcp";
const URL_HTTP = "http://choregos.internal.example/mcp";
const TOKEN = "chg_mcp_token";

describe("snippets of the Integrations page (ADR 0030)", () => {
  it("Claude Code: the exact command, the token passed through a variable", () => {
    expect(extrait("claude-code", URL_HTTPS, TOKEN)?.code).toBe(
      [
        `export CHOREGOS_TOKEN=${TOKEN}`,
        `claude mcp add --transport http choregos ${URL_HTTPS} --header "Authorization: Bearer $CHOREGOS_TOKEN"`,
      ].join("\n"),
    );
  });

  it("the JSON snippets are valid JSON", () => {
    for (const client of ["claude-desktop", "cursor", "vscode"] as const) {
      const code = extrait(client, URL_HTTPS, TOKEN)?.code ?? "";
      expect(() => JSON.parse(code), client).not.toThrow();
    }
  });

  it("--allow-http only for an http: URL", () => {
    expect(extrait("claude-desktop", URL_HTTP, TOKEN)?.code).toContain("--allow-http");
    expect(extrait("claude-desktop", URL_HTTPS, TOKEN)?.code).not.toContain("--allow-http");
  });

  it("VS Code and Cursor do not keep the token in the file", () => {
    expect(extrait("vscode", URL_HTTPS, TOKEN)?.code).not.toContain(TOKEN);
    expect(extrait("cursor", URL_HTTPS, TOKEN)?.code).not.toContain(TOKEN);
  });

  it("the CLI and the REST API never receive the MCP token: they ask for a full-access token", () => {
    for (const client of ["cli", "rest"] as const) {
      const code = extrait(client, URL_HTTPS, TOKEN)?.code ?? "";
      expect(code).not.toContain(TOKEN);
      expect(code).toContain("<full-access token>");
      expect(code).toContain("https://choregos.example");
    }
  });

  it("claude.ai and ChatGPT have no snippet as long as OAuth and a public address are needed", () => {
    expect(extrait("claude-ai", URL_HTTPS)).toBeNull();
    expect(extrait("chatgpt", URL_HTTPS)).toBeNull();
  });

  it("without a minted token, the snippet carries a placeholder to fill in", () => {
    expect(extrait("claude-code", URL_HTTPS)?.code).toContain(PLACEHOLDER);
  });

  it("a project's gate, and the platform's origin", () => {
    expect(gateUrl("https://choregos.example/mcp/", "acme:billing")).toBe(
      "https://choregos.example/mcp/projects/acme:billing",
    );
    expect(gateUrl("https://choregos.example/mcp")).toBe("https://choregos.example/mcp");
    expect(origineDe("https://choregos.example/mcp/projects/acme:billing")).toBe("https://choregos.example");
  });

  it("nine clients, and each has an honest status", () => {
    expect(CLIENTS).toHaveLength(9);
    const distants = CLIENTS.filter((c) => c.calls_from === "the vendor's cloud").map((c) => c.id);
    expect(distants).toEqual(["claude-ai", "chatgpt"]);
  });
});

const OAUTH: OAuth = {
  enabled: true,
  authorization_server: "https://sso.example/realms/platform",
  clients: {
    "claude-code": { client_id: "choregos-claude-code", callback_port: 33418 },
    "claude-ai": { client_id: "choregos-claude-ai", callback_port: null },
  },
};

describe("sign-in through the IdP, when the gate accepts it (S15-09)", () => {
  it("Claude Code: the registered client and its port, no token", () => {
    expect(extrait("claude-code", URL_HTTP, TOKEN, OAUTH)?.code).toBe(
      `claude mcp add --transport http --client-id choregos-claude-code --callback-port 33418 choregos ${URL_HTTP}`,
    );
    expect(extrait("claude-code", URL_HTTP, TOKEN, OAUTH)?.code).not.toContain(TOKEN);
  });

  it("without a registered port, Claude Code picks its own", () => {
    const sansPort: OAuth = { enabled: true, clients: { "claude-code": { client_id: "cc" } } };
    expect(extrait("claude-code", URL_HTTPS, TOKEN, sansPort)?.code).toBe(
      `claude mcp add --transport http --client-id cc choregos ${URL_HTTPS}`,
    );
  });

  it("a gate without OAuth ignores the clients: the token stays the only way", () => {
    const eteinte: OAuth = { ...OAUTH, enabled: false };
    expect(clientOAuth("claude-code", eteinte)).toBeNull();
    expect(extrait("claude-code", URL_HTTPS, TOKEN, eteinte)?.code).toContain(TOKEN);
  });

  it("claude.ai: the client id on an HTTPS address, never a secret", () => {
    const code = extrait("claude-ai", URL_HTTPS, TOKEN, OAUTH)?.code ?? "";
    expect(code).toContain(`Remote MCP server URL: ${URL_HTTPS}`);
    expect(code).toContain("OAuth Client ID:       choregos-claude-ai");
    expect(code).not.toContain(TOKEN);
    expect(code).toMatch(/never shown/);
  });

  it("claude.ai does not reach an http address, OAuth or not", () => {
    expect(extrait("claude-ai", URL_HTTP, TOKEN, OAUTH)).toBeNull();
    const info = CLIENTS.find((c) => c.id === "claude-ai")!;
    expect(clientState(info, URL_HTTP, OAUTH).status).toBe("needs a public HTTPS address");
    expect(clientState(info, URL_HTTPS, OAUTH).status).toBe("ready");
    expect(clientState(info, URL_HTTPS, null).status).toBe("needs a public HTTPS address and OAuth");
  });

  it("a client without a registered client keeps its status", () => {
    const cursor = CLIENTS.find((c) => c.id === "cursor")!;
    expect(clientState(cursor, URL_HTTPS, OAUTH)).toEqual(cursor);
    const code = CLIENTS.find((c) => c.id === "claude-code")!;
    expect(clientState(code, URL_HTTP, OAUTH).auth).toBe("OAuth");
  });
});
