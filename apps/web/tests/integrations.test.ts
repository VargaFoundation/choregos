import { describe, expect, it } from "vitest";
import { CLIENTS, clientOAuth, etatDuClient, extrait, type OAuth, origineDe, PLACEHOLDER, urlDeLaPorte } from "@/lib/integrations";

const URL_HTTPS = "https://choregos.example/mcp";
const URL_HTTP = "http://choregos.internal.example/mcp";
const JETON = "chg_jeton_mcp";

describe("extraits de la page Integrations (ADR 0030)", () => {
  it("Claude Code : la commande exacte, le jeton passé par une variable", () => {
    expect(extrait("claude-code", URL_HTTPS, JETON)?.code).toBe(
      [
        `export CHOREGOS_TOKEN=${JETON}`,
        `claude mcp add --transport http choregos ${URL_HTTPS} --header "Authorization: Bearer $CHOREGOS_TOKEN"`,
      ].join("\n"),
    );
  });

  it("les extraits JSON sont du JSON valide", () => {
    for (const client of ["claude-desktop", "cursor", "vscode"] as const) {
      const code = extrait(client, URL_HTTPS, JETON)?.code ?? "";
      expect(() => JSON.parse(code), client).not.toThrow();
    }
  });

  it("--allow-http seulement pour une URL en http:", () => {
    expect(extrait("claude-desktop", URL_HTTP, JETON)?.code).toContain("--allow-http");
    expect(extrait("claude-desktop", URL_HTTPS, JETON)?.code).not.toContain("--allow-http");
  });

  it("VS Code et Cursor ne gardent pas le jeton dans le fichier", () => {
    expect(extrait("vscode", URL_HTTPS, JETON)?.code).not.toContain(JETON);
    expect(extrait("cursor", URL_HTTPS, JETON)?.code).not.toContain(JETON);
  });

  it("la CLI et l'API REST ne reçoivent jamais le jeton MCP : elles demandent un jeton complet", () => {
    for (const client of ["cli", "rest"] as const) {
      const code = extrait(client, URL_HTTPS, JETON)?.code ?? "";
      expect(code).not.toContain(JETON);
      expect(code).toContain("<full-access token>");
      expect(code).toContain("https://choregos.example");
    }
  });

  it("claude.ai et ChatGPT n'ont pas d'extrait tant qu'il faut OAuth et une adresse publique", () => {
    expect(extrait("claude-ai", URL_HTTPS)).toBeNull();
    expect(extrait("chatgpt", URL_HTTPS)).toBeNull();
  });

  it("sans jeton frappé, l'extrait porte un emplacement à remplir", () => {
    expect(extrait("claude-code", URL_HTTPS)?.code).toContain(PLACEHOLDER);
  });

  it("la porte d'un projet, et l'origine de la plateforme", () => {
    expect(urlDeLaPorte("https://choregos.example/mcp/", "acme:billing")).toBe(
      "https://choregos.example/mcp/projects/acme:billing",
    );
    expect(urlDeLaPorte("https://choregos.example/mcp")).toBe("https://choregos.example/mcp");
    expect(origineDe("https://choregos.example/mcp/projects/acme:billing")).toBe("https://choregos.example");
  });

  it("neuf clients, et chacun a un statut honnête", () => {
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

describe("la connexion par l'IdP, quand la porte l'accepte (S15-09)", () => {
  it("Claude Code : le client enregistré et son port, aucun jeton", () => {
    expect(extrait("claude-code", URL_HTTP, JETON, OAUTH)?.code).toBe(
      `claude mcp add --transport http --client-id choregos-claude-code --callback-port 33418 choregos ${URL_HTTP}`,
    );
    expect(extrait("claude-code", URL_HTTP, JETON, OAUTH)?.code).not.toContain(JETON);
  });

  it("sans port enregistré, Claude Code choisit le sien", () => {
    const sansPort: OAuth = { enabled: true, clients: { "claude-code": { client_id: "cc" } } };
    expect(extrait("claude-code", URL_HTTPS, JETON, sansPort)?.code).toBe(
      `claude mcp add --transport http --client-id cc choregos ${URL_HTTPS}`,
    );
  });

  it("une porte sans OAuth ignore les clients : le jeton reste le seul chemin", () => {
    const eteinte: OAuth = { ...OAUTH, enabled: false };
    expect(clientOAuth("claude-code", eteinte)).toBeNull();
    expect(extrait("claude-code", URL_HTTPS, JETON, eteinte)?.code).toContain(JETON);
  });

  it("claude.ai : l'identifiant du client sur une adresse HTTPS, jamais un secret", () => {
    const code = extrait("claude-ai", URL_HTTPS, JETON, OAUTH)?.code ?? "";
    expect(code).toContain(`Remote MCP server URL: ${URL_HTTPS}`);
    expect(code).toContain("OAuth Client ID:       choregos-claude-ai");
    expect(code).not.toContain(JETON);
    expect(code).toMatch(/never shown/);
  });

  it("claude.ai ne joint pas une adresse en http, OAuth ou pas", () => {
    expect(extrait("claude-ai", URL_HTTP, JETON, OAUTH)).toBeNull();
    const info = CLIENTS.find((c) => c.id === "claude-ai")!;
    expect(etatDuClient(info, URL_HTTP, OAUTH).status).toBe("needs a public HTTPS address");
    expect(etatDuClient(info, URL_HTTPS, OAUTH).status).toBe("ready");
    expect(etatDuClient(info, URL_HTTPS, null).status).toBe("needs a public HTTPS address and OAuth");
  });

  it("un client sans client enregistré garde son statut", () => {
    const cursor = CLIENTS.find((c) => c.id === "cursor")!;
    expect(etatDuClient(cursor, URL_HTTPS, OAUTH)).toEqual(cursor);
    const code = CLIENTS.find((c) => c.id === "claude-code")!;
    expect(etatDuClient(code, URL_HTTP, OAUTH).auth).toBe("OAuth");
  });
});
