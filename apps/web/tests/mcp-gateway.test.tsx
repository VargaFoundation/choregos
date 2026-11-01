import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { McpGatewayCard } from "@/components/admin/mcp-gateway";
import { api, ApiError } from "@/lib/api";
import { endpointProblem, isPluginServer } from "@/lib/mcp-gateway";
import type { McpGatewayState, OrgConnector } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      mcpGateway: vi.fn(),
      putMcpGateway: vi.fn(),
      deleteMcpGateway: vi.fn(),
      testMcpGateway: vi.fn(),
      rotateMcpGatewayAssertionKey: vi.fn(),
      orgConnectors: vi.fn(),
    },
  };
});

function wrap(node: React.ReactNode) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{node}</QueryClientProvider>);
}

const ASSERTION = {
  issuer: "https://choregos.example.com/orgs/acme",
  jwks_url: "https://choregos.example.com/api/v1/orgs/acme/mcp-gateway/jwks.json",
  keys: [],
};
const NONE: McpGatewayState = { gateway: null, updated_by: null, updated_at: null, assertion: ASSERTION };
const STORED: McpGatewayState = {
  gateway: {
    mode: "available",
    endpoint: "https://gw.example.com/mcp/{server}",
    routing: "path",
    server_header: null,
    auth: { type: "bearer" },
    audience: null,
    identity_header: null,
    timeout_s: 30,
    secret_set: true,
  },
  updated_by: "lea@varga.dev",
  updated_at: "2026-10-26T10:00:00Z",
  assertion: ASSERTION,
};
const connector = (over: Partial<OrgConnector>): OrgConnector => ({ name: "x", kind: "mcp", type: "mcp", config: {}, status: "ok", ...over });

const save = () => screen.getByRole("button", { name: "Save MCP gateway" });

describe("the organisation's MCP gateway (ADR 0105)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(api.mcpGateway).mockResolvedValue(NONE);
    vi.mocked(api.orgConnectors).mockResolvedValue([connector({ name: "supplier-agent" })]);
  });

  it("renders nothing for a non-admin", () => {
    wrap(<McpGatewayCard org="acme" canEdit={false} />);
    expect(screen.queryByText("MCP gateway")).not.toBeInTheDocument();
    expect(api.mcpGateway).not.toHaveBeenCalled();
  });

  it("saves a bearer gateway and never shows the secret again", async () => {
    vi.mocked(api.putMcpGateway).mockImplementation(async () => {
      vi.mocked(api.mcpGateway).mockResolvedValue(STORED);
      return STORED;
    });
    wrap(<McpGatewayCard org="acme" canEdit />);
    await screen.findByLabelText("Endpoint");
    fireEvent.change(screen.getByLabelText("Endpoint"), { target: { value: "https://gw.example.com/mcp/{server}" } });
    expect(save()).toBeDisabled(); // the first time, a secret is required
    fireEvent.change(screen.getByLabelText("Secret"), { target: { value: "s3cret" } });
    fireEvent.click(save());
    await waitFor(() => expect(api.putMcpGateway).toHaveBeenCalled());
    expect(vi.mocked(api.putMcpGateway).mock.calls[0]![1]).toMatchObject({
      endpoint: "https://gw.example.com/mcp/{server}",
      mode: "available",
      secret: "s3cret",
    });
    await screen.findByText("set");
    expect((screen.getByLabelText("Secret") as HTMLInputElement).value).toBe("");
  });

  it("blocks an endpoint without {server} for path routing", async () => {
    wrap(<McpGatewayCard org="acme" canEdit />);
    await screen.findByLabelText("Endpoint");
    fireEvent.change(screen.getByLabelText("Endpoint"), { target: { value: "https://gw.example.com/mcp" } });
    fireEvent.change(screen.getByLabelText("Secret"), { target: { value: "s3cret" } });
    expect(save()).toBeDisabled();
    expect(screen.getByText(endpointProblem("https://gw.example.com/mcp", "path")!)).toBeInTheDocument();
  });

  it("shows the issuer and the JWKS URL for jwt_assertion", async () => {
    vi.mocked(api.rotateMcpGatewayAssertionKey).mockResolvedValue(ASSERTION);
    wrap(<McpGatewayCard org="acme" canEdit />);
    await screen.findByLabelText("Endpoint");
    fireEvent.change(screen.getByLabelText("Endpoint"), { target: { value: "https://gw.example.com/mcp/{server}" } });
    fireEvent.change(screen.getByLabelText("Auth type"), { target: { value: "jwt_assertion" } });
    fireEvent.change(screen.getByLabelText("Audience"), { target: { value: "gateway" } });
    expect(screen.queryByLabelText("Secret")).not.toBeInTheDocument();
    fireEvent.click(save());
    await waitFor(() => expect(api.putMcpGateway).toHaveBeenCalled());
    expect(vi.mocked(api.putMcpGateway).mock.calls[0]![1]).toMatchObject({ auth: { type: "jwt_assertion" }, audience: "gateway" });
    expect(screen.getByText(ASSERTION.jwks_url)).toBeInTheDocument();
    expect(screen.getByText(ASSERTION.issuer)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Rotate key" }));
    fireEvent.click(screen.getByRole("button", { name: "Rotate the key" }));
    await waitFor(() => expect(api.rotateMcpGatewayAssertionKey).toHaveBeenCalledWith("acme"));
  });

  it("warns before required mode stops direct MCP connectors", async () => {
    wrap(<McpGatewayCard org="acme" canEdit />);
    await screen.findByLabelText("Mode");
    expect(screen.queryByText("supplier-agent")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Mode"), { target: { value: "required" } });
    expect(await screen.findByText("supplier-agent")).toBeInTheDocument();
  });

  it("does not count a plugin's server as a direct connector", async () => {
    const desk = connector({ name: "desk", config: {}, catalogue_entry: "plugin:desk/desk@0.1.0", created_by: "plugin:desk" });
    vi.mocked(api.orgConnectors).mockResolvedValue([desk, connector({ name: "supplier-agent" })]);
    expect(isPluginServer(desk)).toBe(true);
    wrap(<McpGatewayCard org="acme" canEdit />);
    await screen.findByLabelText("Mode");
    fireEvent.change(screen.getByLabelText("Mode"), { target: { value: "required" } });
    await screen.findByText("supplier-agent");
    expect(screen.queryByText("desk")).not.toBeInTheDocument();
  });

  it("reports a test result", async () => {
    vi.mocked(api.mcpGateway).mockResolvedValue(STORED);
    vi.mocked(api.testMcpGateway).mockResolvedValue({
      ok: true,
      server: "supplier",
      protocol_version: "2025-06-18",
      server_info: { name: "supplier", version: "1.0.0" },
      tools: 3,
      elapsed_ms: 120,
      error: null,
    });
    wrap(<McpGatewayCard org="acme" canEdit />);
    await screen.findByLabelText("Server to test");
    fireEvent.change(screen.getByLabelText("Server to test"), { target: { value: "supplier" } });
    fireEvent.click(screen.getByRole("button", { name: "Test the gateway" }));
    expect(await screen.findByText(/ok: server supplier/)).toHaveTextContent("3 tools");
    expect(api.testMcpGateway).toHaveBeenCalledWith("acme", { server: "supplier" });
  });

  it("names the connectors when removal is refused", async () => {
    vi.mocked(api.mcpGateway).mockResolvedValue(STORED);
    vi.mocked(api.deleteMcpGateway).mockRejectedValue(
      new ApiError(409, { title: "Conflict", code: "gateway_in_use", connectors: ["crm", "erp"] }),
    );
    wrap(<McpGatewayCard org="acme" canEdit />);
    await screen.findByDisplayValue("https://gw.example.com/mcp/{server}");
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove the gateway" }));
    expect(await screen.findByText(/Still used by: crm, erp/)).toBeInTheDocument();
  });
});
