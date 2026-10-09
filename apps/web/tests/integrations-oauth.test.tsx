import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { IntegrationsPanel } from "@/components/integrations/panel";
import { api } from "@/lib/api";
import type { Integrations } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return { ...reel, api: { ...reel.api, integrations: vi.fn() } };
});

const AVEC_OAUTH: Integrations = {
  mcp_url: "http://choregos.internal.example/mcp",
  protocol_versions: ["2025-06-18"],
  oauth: {
    enabled: true,
    authorization_server: "https://sso.example/realms/platform",
    clients: { "claude-code": { client_id: "choregos-claude-code", callback_port: 33418 } },
  },
  version: "0.16.3",
};

function rendre(client: "claude-code" | "cursor") {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <IntegrationsPanel client={client} base="/integrations" />
    </QueryClientProvider>,
  );
}

describe("la page Integrations propose l'authentification unique (S15-09)", () => {
  it("Claude Code : la commande OAuth d'abord, le jeton sur demande", async () => {
    vi.mocked(api.integrations).mockResolvedValue(AVEC_OAUTH);
    rendre("claude-code");
    const extrait = await screen.findByText(/--client-id choregos-claude-code --callback-port 33418/);
    expect(extrait).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /create an access token/ })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "use an MCP token instead" }));
    expect(await screen.findByText(/--header "Authorization: Bearer \$CHOREGOS_TOKEN"/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /create an access token/ })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "sign in with single sign-on instead" }));
    expect(await screen.findByText(/--client-id choregos-claude-code/)).toBeInTheDocument();
  });

  it("un client que l'IdP ne connaît pas garde son jeton, sans proposer l'authentification unique", async () => {
    vi.mocked(api.integrations).mockResolvedValue(AVEC_OAUTH);
    rendre("cursor");
    expect(await screen.findByRole("button", { name: /create an access token/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "use an MCP token instead" })).not.toBeInTheDocument();
  });
});
