import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import McpCataloguePage from "@/app/library/mcp-servers/page";
import type { McpCatalogue, McpCatalogueEntry } from "@/lib/types";
import file from "../src/mocks/mcp-catalogue.json";

const { push, install, discover, gatewayRoutes } = vi.hoisted(() => ({
  push: vi.fn(),
  install: vi.fn(),
  discover: vi.fn(),
  // Per entry id: what the API says about this organisation's routes; absent means a deployment without gateway.
  gatewayRoutes: new Map<string, Array<"direct" | "gateway">>(),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/library/mcp-servers",
  useRouter: () => ({ push }),
}));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme" }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      mcpCatalogue: vi.fn(async () => withRoutes()),
      installMcpCatalogueEntry: install,
      discoverConnectorOperations: discover,
    },
  };
});

function withRoutes(): McpCatalogue {
  const entries = (file.entries as unknown as McpCatalogueEntry[]).map(
    (entry) => {
      const routes = gatewayRoutes.get(entry.id);
      const direct = entry.install === "installable";
      return {
        ...structuredClone(entry),
        can: {
          install: routes ? routes.length > 0 : direct,
          configure_projects: [],
          enable_projects: [],
        },
        cannot_reason: null,
        enablement: [],
        ...(routes ? { install_routes: routes } : {}),
      };
    },
  );
  return { ...structuredClone(file), entries } as unknown as McpCatalogue;
}

function page() {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <McpCataloguePage />
    </QueryClientProvider>,
  );
}

describe("the catalogue through the MCP gateway (ADR 0105)", () => {
  it("a hosting entry is installable through the gateway", async () => {
    gatewayRoutes.clear();
    gatewayRoutes.set("playwright", ["gateway"]);
    push.mockReset();
    install.mockReset();
    install.mockResolvedValue({
      name: "playwright",
      kind: "mcp",
      type: "mcp-gateway",
      operations: [],
    });
    discover.mockResolvedValue({
      added: [],
      changed: [],
      removed: [],
      unchanged: 0,
    });
    page();
    const card = await screen.findByTestId("entry-playwright");
    expect(
      within(card).getByText("Through your MCP gateway"),
    ).toBeInTheDocument();
    const button = within(card).getByRole("button", { name: "Install" });
    expect(button).toBeEnabled();
    expect(card).not.toHaveTextContent("hosting a third party's code");
    fireEvent.click(button);
    const drawer = screen.getByRole("dialog");
    expect(within(drawer).queryByLabelText("token value")).toBeNull();
    expect(
      within(drawer).getByLabelText("Server name at the gateway"),
    ).toHaveValue("playwright");
    fireEvent.click(within(drawer).getByRole("button", { name: "Install" }));
    await waitFor(() => expect(push).toHaveBeenCalled());
    expect(install).toHaveBeenCalledWith("acme", "playwright", {
      name: "playwright",
      route: "gateway",
      gateway_server: "playwright",
      availability: "selected_projects",
    });
    expect(install.mock.calls[0]![2]).not.toHaveProperty("secret_refs");
  });

  it("an installable entry offers both routes and keeps the direct one by default", async () => {
    gatewayRoutes.clear();
    gatewayRoutes.set("github", ["direct", "gateway"]);
    push.mockReset();
    install.mockReset();
    install.mockResolvedValue({
      name: "github",
      kind: "mcp",
      type: "mcp",
      operations: [],
    });
    discover.mockResolvedValue({
      added: [],
      changed: [],
      removed: [],
      unchanged: 0,
    });
    page();
    const card = await screen.findByTestId("entry-github");
    expect(within(card).queryByText("Through your MCP gateway")).toBeNull();
    fireEvent.click(within(card).getByRole("button", { name: "Install" }));
    const drawer = screen.getByRole("dialog");
    const direct = within(drawer).getByLabelText(
      "Directly (Choregos holds the credential)",
    );
    const gateway = within(drawer).getByLabelText(
      "Through the organisation's MCP gateway (the gateway holds it)",
    );
    expect(direct).toBeChecked();
    expect(gateway).not.toBeChecked();
    expect(
      within(drawer).queryByLabelText("Server name at the gateway"),
    ).toBeNull();
    fireEvent.change(within(drawer).getByLabelText("token value"), {
      target: { value: "ghp_typed_once" },
    });
    fireEvent.click(within(drawer).getByRole("button", { name: "Install" }));
    await waitFor(() => expect(push).toHaveBeenCalled());
    expect(install).toHaveBeenCalledWith("acme", "github", {
      name: "github",
      secret_refs: {},
      secret_values: { token: "ghp_typed_once" },
      availability: "selected_projects",
    });
  });

  it("choosing the gateway hides the secret and refuses a server name the gateway cannot hold", async () => {
    gatewayRoutes.clear();
    gatewayRoutes.set("github", ["direct", "gateway"]);
    page();
    fireEvent.click(
      within(await screen.findByTestId("entry-github")).getByRole("button", {
        name: "Install",
      }),
    );
    const drawer = screen.getByRole("dialog");
    fireEvent.click(
      within(drawer).getByLabelText(
        "Through the organisation's MCP gateway (the gateway holds it)",
      ),
    );
    expect(within(drawer).queryByLabelText("token value")).toBeNull();
    fireEvent.change(
      within(drawer).getByLabelText("Server name at the gateway"),
      { target: { value: "bad name/" } },
    );
    expect(
      within(drawer).getByRole("button", { name: "Install" }),
    ).toBeDisabled();
  });

  it("without a gateway nothing changes", async () => {
    gatewayRoutes.clear();
    page();
    const playwright = await screen.findByTestId("entry-playwright");
    expect(
      within(playwright).getByRole("button", { name: "Install" }),
    ).toBeDisabled();
    expect(playwright).toHaveTextContent("hosting a third party's code");
    expect(
      within(playwright).queryByText("Through your MCP gateway"),
    ).toBeNull();
    expect(
      within(screen.getByTestId("entry-github")).getByRole("button", {
        name: "Install",
      }),
    ).toBeEnabled();
  });
});
