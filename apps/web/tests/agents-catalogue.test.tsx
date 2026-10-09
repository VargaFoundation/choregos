import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CatalogueDAgents, etatDeLEntree } from "@/components/agents/catalogue";
import { api } from "@/lib/api";
import type { AgentCatalogueEntry } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
    api: {
      ...reel.api,
      agentCatalogue: vi.fn(),
      installCatalogueAgent: vi.fn(),
      connectCatalogueClient: vi.fn(),
      integrations: vi.fn().mockResolvedValue({ mcp_url: "https://choregos.example/mcp", protocol_versions: [], oauth: { enabled: false } }),
    },
  };
});

const entree = (slug: string, extra: Partial<AgentCatalogueEntry> = {}): AgentCatalogueEntry => ({
  slug, kind: "internal", display_name: slug, role: "implement", summary: "s", version: 1, installed: false,
  installed_version: null, own_agent: false, update_available: false, offered: true, skills: [], ...extra,
});

function rendre() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <CatalogueDAgents org="varga" />
    </QueryClientProvider>,
  );
}

describe("le catalogue d'agents dans la console (S21-18)", () => {
  beforeEach(() => {
    vi.mocked(api.agentCatalogue).mockResolvedValue([
      entree("developer"),
      entree("tester", { installed: true, installed_version: 1 }),
      entree("reviewer", { installed: true, installed_version: 2, update_available: true }),
      entree("architect", { installed: true, installed_version: 1, own_agent: true }),
      entree("claude-code", { kind: "external", role: "external", client: "claude-code", reach: "always" }),
      entree("chatgpt", { kind: "external", role: "external", client: "chatgpt", reach: "cloud", offered: false, unavailable_reason: "no OAuth here" }),
    ]);
    vi.mocked(api.installCatalogueAgent).mockReset().mockResolvedValue({} as never);
    vi.mocked(api.connectCatalogueClient).mockReset();
  });

  it("dit ce que fera le bouton : installer, mettre à jour, rien — et ne touche pas à un agent de l'organisation", () => {
    expect(etatDeLEntree(entree("a")).action).toBe("install");
    expect(etatDeLEntree(entree("a", { installed: true, installed_version: 2, update_available: true }))).toEqual({
      libelle: "installed v2 — an update is available",
      action: "update",
    });
    expect(etatDeLEntree(entree("a", { installed: true, installed_version: 1 })).action).toBeNull();
    expect(etatDeLEntree(entree("a", { installed: true, own_agent: true })).action).toBeNull();
  });

  it("installe un agent, met à jour un autre, et laisse celui de l'organisation", async () => {
    rendre();
    const developer = await screen.findByTestId("catalogue-developer");
    fireEvent.click(within(developer).getByRole("button", { name: "install" }));
    await waitFor(() => expect(api.installCatalogueAgent).toHaveBeenLastCalledWith("varga", "developer", false));
    fireEvent.click(within(screen.getByTestId("catalogue-reviewer")).getByRole("button", { name: "update" }));
    await waitFor(() => expect(api.installCatalogueAgent).toHaveBeenLastCalledWith("varga", "reviewer", true));
    expect(within(screen.getByTestId("catalogue-architect")).queryByRole("button")).toBeNull();
    expect(screen.getByTestId("catalogue-architect")).toHaveTextContent("your organisation has its own agent");
  });

  it("connecte Claude Code en un clic et montre sa configuration, avec le jeton, une fois", async () => {
    vi.mocked(api.connectCatalogueClient).mockResolvedValue({
      agent: { slug: "claude-code", kind: "external", display_name: "Claude Code", status: "active", latest_version: 1 },
      token: { id: "t", name: "Claude Code", created_at: "2026-10-07T00:00:00Z", scopes: ["mcp:write"], token: "chg_secret_once" },
      oauth_client_id: null,
    } as never);
    rendre();
    const claude = await screen.findByTestId("client-catalogue-claude-code");
    fireEvent.click(within(claude).getByLabelText("read only"));
    fireEvent.click(within(claude).getByRole("button", { name: "connect" }));
    await waitFor(() => expect(api.connectCatalogueClient).toHaveBeenCalledWith("varga", "claude-code", true));
    const extrait = await screen.findByTestId("extrait-claude-code");
    expect(extrait).toHaveTextContent("chg_secret_once");
    expect(extrait).toHaveTextContent("https://choregos.example/mcp");
    expect(within(claude).getByRole("button", { name: "connected" })).toBeDisabled();
  });

  it("un client du cloud que la porte n'accepte pas encore se dit, sans bouton", async () => {
    rendre();
    const chatgpt = await screen.findByTestId("client-catalogue-chatgpt");
    expect(chatgpt).toHaveTextContent("not available here");
    expect(chatgpt).toHaveTextContent("Why: no OAuth here");
    expect(within(chatgpt).queryByRole("button", { name: "connect" })).toBeNull();
  });
});
