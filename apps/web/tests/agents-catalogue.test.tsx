import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AgentCatalogue, AiClientCatalogue, entryState } from "@/components/agents/catalogue";
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

const entry = (slug: string, extra: Partial<AgentCatalogueEntry> = {}): AgentCatalogueEntry => ({
  slug, kind: "internal", display_name: slug, role: "implement", summary: "s", version: 1, installed: false,
  installed_version: null, own_agent: false, update_available: false, offered: true, skills: [], ...extra,
});

function mount() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AgentCatalogue org="acme" />
      <AiClientCatalogue org="acme" />
    </QueryClientProvider>,
  );
}

describe("the agent catalogue in the console (S21-18)", () => {
  beforeEach(() => {
    vi.mocked(api.agentCatalogue).mockResolvedValue([
      entry("developer"),
      entry("tester", { installed: true, installed_version: 1 }),
      entry("reviewer", { installed: true, installed_version: 2, update_available: true }),
      entry("architect", { installed: true, installed_version: 1, own_agent: true }),
      entry("claude-code", { kind: "external", role: "external", client: "claude-code", reach: "always" }),
      entry("chatgpt", { kind: "external", role: "external", client: "chatgpt", reach: "cloud", offered: false, unavailable_reason: "no OAuth here" }),
    ]);
    vi.mocked(api.installCatalogueAgent).mockReset().mockResolvedValue({} as never);
    vi.mocked(api.connectCatalogueClient).mockReset();
  });

  it("says what the button will do: install, update, nothing, and does not touch an organisation agent", () => {
    expect(entryState(entry("a")).action).toBe("install");
    expect(entryState(entry("a", { installed: true, installed_version: 2, update_available: true }))).toEqual({
      libelle: "Installed v2 — an update is available",
      action: "update",
    });
    expect(entryState(entry("a", { installed: true, installed_version: 1 })).action).toBeNull();
    expect(entryState(entry("a", { installed: true, own_agent: true })).action).toBeNull();
  });

  it("installs one agent, updates another, and leaves the organisation's", async () => {
    mount();
    const developer = await screen.findByTestId("catalogue-developer");
    fireEvent.click(within(developer).getByRole("button", { name: "Install" }));
    await waitFor(() => expect(api.installCatalogueAgent).toHaveBeenLastCalledWith("acme", "developer", false));
    fireEvent.click(within(screen.getByTestId("catalogue-reviewer")).getByRole("button", { name: "Update" }));
    await waitFor(() => expect(api.installCatalogueAgent).toHaveBeenLastCalledWith("acme", "reviewer", true));
    expect(within(screen.getByTestId("catalogue-architect")).queryByRole("button")).toBeNull();
    expect(screen.getByTestId("catalogue-architect")).toHaveTextContent("Your organisation has its own agent");
  });

  it("connects Claude Code in one click and shows its configuration, with the token, once", async () => {
    vi.mocked(api.connectCatalogueClient).mockResolvedValue({
      agent: { slug: "claude-code", kind: "external", display_name: "Claude Code", status: "active", latest_version: 1 },
      token: { id: "t", name: "Claude Code", created_at: "2026-10-07T00:00:00Z", scopes: ["mcp:write"], token: "chg_secret_once" },
      oauth_client_id: null,
    } as never);
    mount();
    const claude = await screen.findByTestId("client-catalogue-claude-code");
    fireEvent.click(within(claude).getByLabelText("Read only"));
    fireEvent.click(within(claude).getByRole("button", { name: "Connect" }));
    await waitFor(() => expect(api.connectCatalogueClient).toHaveBeenCalledWith("acme", "claude-code", true));
    const extrait = await screen.findByTestId("extrait-claude-code");
    expect(extrait).toHaveTextContent("chg_secret_once");
    expect(extrait).toHaveTextContent("https://choregos.example/mcp");
    expect(within(claude).getByRole("button", { name: "Connected" })).toBeDisabled();
  });

  it("a cloud client that the door does not accept yet says so, with no button", async () => {
    mount();
    const chatgpt = await screen.findByTestId("client-catalogue-chatgpt");
    expect(chatgpt).toHaveTextContent("Not available here");
    expect(chatgpt).toHaveTextContent("Why: no OAuth here");
    expect(within(chatgpt).queryByRole("button", { name: "Connect" })).toBeNull();
  });
});
