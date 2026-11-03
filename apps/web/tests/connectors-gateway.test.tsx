import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import OrgConnectorsPage from "@/app/admin/connectors/page";
import type { ConnectorType, McpGatewayState, OrgConnector } from "@/lib/types";

vi.mock("next/navigation", () => ({
  usePathname: () => "/admin/connectors",
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme" }) }));

const { state, through, gatewayType } = vi.hoisted(() => ({
  state: { gateway: null as unknown },
  through: {
    name: "playwright",
    kind: "mcp",
    type: "mcp-gateway",
    config: { server: "pw-prod" },
    operations: [],
  } as unknown as OrgConnector,
  gatewayType: {
    kind: "mcp",
    type: "mcp-gateway",
    display: "MCP server through the organisation's MCP gateway",
    config_schema: {
      type: "object",
      properties: { server: { type: "string" } },
      required: ["server"],
    },
    secret_fields: [],
  } as unknown as ConnectorType,
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      orgConnectors: vi.fn().mockResolvedValue([through]),
      projects: vi
        .fn()
        .mockResolvedValue({ items: [], meta: { has_more: false } }),
      connectors: vi.fn().mockResolvedValue([]),
      connectorTypes: vi.fn().mockResolvedValue([gatewayType]),
      mcpGateway: vi.fn(
        async () =>
          ({
            gateway: state.gateway,
            updated_by: null,
            updated_at: null,
            assertion: { issuer: "", jwks_url: "", keys: [] },
          }) as McpGatewayState,
      ),
    },
  };
});

function page() {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <OrgConnectorsPage />
    </QueryClientProvider>,
  );
}

async function chooseGatewayType() {
  await screen.findByTestId("connector-playwright");
  fireEvent.change(screen.getByPlaceholderText("entra-acme"), {
    target: { value: "pw" },
  });
  const select = screen.getByRole("combobox", { name: "Type" });
  fireEvent.change(select, { target: { value: "mcp/mcp-gateway" } });
}

describe("connectors through the MCP gateway (ADR 0105)", () => {
  it("a gateway connector shows its route and server", async () => {
    state.gateway = null;
    page();
    const card = await screen.findByTestId("connector-playwright");
    expect(within(card).getByTestId("route-playwright")).toHaveTextContent(
      "via gateway: pw-prod",
    );
  });

  it("declaring a gateway connector without a gateway explains why", async () => {
    state.gateway = null;
    page();
    await chooseGatewayType();
    expect(
      await screen.findByText(/Set the organisation's MCP gateway first/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Administration" }),
    ).toHaveAttribute("href", "/admin");
    expect(screen.getByRole("button", { name: "Declare" })).toBeDisabled();
  });

  it("with a gateway available the note goes away and the connector can be declared", async () => {
    state.gateway = {
      mode: "available",
      endpoint: "https://gw.example.com/mcp/{server}",
      routing: "path",
      auth: { type: "bearer" },
      timeout_s: 30,
      secret_set: true,
    };
    page();
    await chooseGatewayType();
    fireEvent.change(await screen.findByLabelText(/^server/), {
      target: { value: "pw-prod" },
    });
    expect(
      screen.queryByText(/Set the organisation's MCP gateway first/),
    ).toBeNull();
    expect(screen.getByRole("button", { name: "Declare" })).toBeEnabled();
  });
});
