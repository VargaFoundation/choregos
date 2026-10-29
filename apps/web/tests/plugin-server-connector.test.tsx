import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import OrgConnectorsPage from "@/app/admin/connectors/page";
import type { ConnectorType, OrgConnector } from "@/lib/types";

/**
 * S43-08: a plugin's server is configured in its connector with the SERVER's schema and secret fields
 * (ADR 0096), not the `mcp` type's `url`; the platform's `_server_token` is not offered; Catalogue →
 * Plugins links here with `?connector=<server>`, which marks the connector and opens its settings.
 */
vi.mock("next/navigation", () => ({ usePathname: () => "/admin/connectors", useSearchParams: () => new URLSearchParams() }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme" }) }));

const { servicenow } = vi.hoisted(() => ({
  servicenow: {
    name: "servicenow",
    kind: "mcp",
    type: "mcp",
    status: "unconfigured",
    config: {},
    secrets: [{ field: "_server_token", source: "store", version: 1 }],
    operations: [],
    availability: "all_projects",
    enabled_projects: [],
    server: {
      plugin: "servicenow",
      version: "0.1.0",
      server: "servicenow",
      config_schema: { type: "object", required: ["instance_url"], properties: { instance_url: { type: "string" } } },
      secret_fields: ["client_secret"],
    },
  } as unknown as OrgConnector,
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      orgConnectors: vi.fn().mockResolvedValue([servicenow]),
      projects: vi.fn().mockResolvedValue({ items: [], meta: { has_more: false } }),
      connectorTypes: vi.fn().mockResolvedValue([
        {
          kind: "mcp",
          type: "mcp",
          display: "MCP server",
          config_schema: { type: "object", required: ["url"], properties: { url: { type: "string" } } },
          secret_fields: ["token"],
        } as unknown as ConnectorType,
      ]),
      listProcedures: vi.fn().mockResolvedValue([]),
    },
  };
});

afterEach(() => window.history.replaceState(null, "", "/"));

describe("a plugin server's connector", () => {
  it("opens on ?connector=, with the server's own schema and secrets, never the platform's token", async () => {
    window.history.replaceState(null, "", "/admin/connectors?connector=servicenow");
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <OrgConnectorsPage />
      </QueryClientProvider>,
    );
    const card = await screen.findByTestId("connector-servicenow");
    expect(card).toHaveAttribute("aria-current", "true");
    expect(within(card).getByTestId("plugin-server-servicenow")).toHaveTextContent("The server of the plugin servicenow 0.1.0");
    // Its settings are open, drawn from the server's schema: `instance_url`, not the mcp type's `url`.
    expect(await within(card).findByLabelText(/instance url/)).toBeInTheDocument();
    expect(within(card).queryByLabelText(/^url/)).toBeNull();
    expect(within(card).getByTestId("secret-client_secret")).toBeInTheDocument();
    expect(within(card).queryByTestId("secret-token")).toBeNull();
    expect(within(card).queryByTestId("secret-_server_token")).toBeNull();
  });
});
