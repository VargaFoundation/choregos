import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { A2aAgent } from "@/components/integrations/a2a";
import { api } from "@/lib/api";
import { a2aCardUrl } from "@/lib/integrations";
import type { ApiTokenCreated, Integrations } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, integrations: vi.fn(), createToken: vi.fn() } };
});

const INFOS: Integrations = {
  mcp_url: "https://choregos.example/mcp",
  protocol_versions: ["2025-06-18"],
  oauth: { enabled: false },
  version: "0.16.3",
};

describe("the A2A card on the Integrations page (ADR 0078)", () => {
  it("is beside /mcp, under the organisation", () => {
    expect(a2aCardUrl("https://choregos.example/mcp/", "varga")).toBe(
      "https://choregos.example/a2a/orgs/varga/.well-known/agent-card.json",
    );
  });

  it("shows the card, the endpoint, and creates a token of the a2a scope only", async () => {
    vi.mocked(api.integrations).mockResolvedValue(INFOS);
    vi.mocked(api.createToken).mockResolvedValue({ token: "chg_a2a_once" } as ApiTokenCreated);
    render(
      <QueryClientProvider client={new QueryClient()}>
        <A2aAgent org="varga" />
      </QueryClientProvider>,
    );
    expect(await screen.findByText("https://choregos.example/a2a/orgs/varga/.well-known/agent-card.json")).toBeInTheDocument();
    expect(screen.getByTestId("a2a-endpoint")).toHaveTextContent("https://choregos.example/a2a/orgs/varga");
    fireEvent.click(screen.getByRole("button", { name: "Create an A2A token" }));
    expect(await screen.findByTestId("a2a-token")).toHaveTextContent("chg_a2a_once");
    expect(vi.mocked(api.createToken).mock.calls[0]![0].scopes).toEqual(["a2a"]);
  });
});
