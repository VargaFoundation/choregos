import { describe, expect, it } from "vitest";
import { CLIENTS, extrait, origineDe, PLACEHOLDER, urlDeLaPorte } from "@/lib/integrations";

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
