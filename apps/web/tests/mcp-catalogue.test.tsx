import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import McpCataloguePage from "@/app/catalogue/mcp-servers/page";
import { categoriesOf, matchesText, NO_FILTERS, stars, withToggle } from "@/lib/catalogue";
import { filterMcpEntries, whyNotInstallable } from "@/lib/mcp-catalogue";
import type { McpCatalogue, McpCatalogueEntry } from "@/lib/types";
import file from "../src/mocks/mcp-catalogue.json";

const { push, install, discover, enable, disable, overrides } = vi.hoisted(() => ({
  push: vi.fn(),
  install: vi.fn(),
  discover: vi.fn(),
  enable: vi.fn(),
  disable: vi.fn(),
  // Per entry id: what the server decided for the caller (ADR 0074); the default is an administrator.
  overrides: new Map<string, Record<string, unknown>>(),
}));

vi.mock("next/navigation", () => ({ usePathname: () => "/catalogue/mcp-servers", useRouter: () => ({ push }) }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme" }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      mcpCatalogue: vi.fn(async () => withRights()),
      enableOrgConnector: enable,
      disableOrgConnector: disable,
      installMcpCatalogueEntry: install,
      discoverConnectorOperations: discover,
    },
  };
});

/** The shipped file, as the API would answer an administrator: install where the entry allows it. */
function withRights(): McpCatalogue {
  const entries = (file.entries as unknown as McpCatalogueEntry[]).map((entry) => ({
    ...structuredClone(entry),
    can: { install: entry.install === "installable", configure_projects: [], enable_projects: [] },
    cannot_reason: null,
    enablement: [],
    ...overrides.get(entry.id),
  }));
  return { ...structuredClone(file), entries } as unknown as McpCatalogue;
}

const catalogue = file as unknown as McpCatalogue;

function page() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <McpCataloguePage />
    </QueryClientProvider>,
  );
}

describe("the MCP catalogue's pure parts", () => {
  it("every word must match, in any order", () => {
    expect(matchesText("GitHub source control", "control github")).toBe(true);
    expect(matchesText("GitHub source control", "gitlab")).toBe(false);
    expect(matchesText("anything", "")).toBe(true);
  });

  it("filters by text, category, official and installable", () => {
    const all = catalogue.entries;
    expect(all.length).toBeGreaterThanOrEqual(90);
    expect(filterMcpEntries(all, { ...NO_FILTERS, q: "github" }).map((e) => e.id)).toContain("github");
    const databases = filterMcpEntries(all, { ...NO_FILTERS, category: "databases" });
    expect(databases.length).toBeGreaterThan(3);
    expect(databases.every((e) => e.categories.includes("databases"))).toBe(true);
    const official = filterMcpEntries(all, withToggle(NO_FILTERS, "official", true));
    expect(official.length).toBeLessThan(all.length);
    expect(official.every((e) => e.official)).toBe(true);
    const installable = filterMcpEntries(all, withToggle(NO_FILTERS, "installable", true));
    expect(installable.length).toBeGreaterThan(10);
    expect(installable.every((e) => e.install === "installable")).toBe(true);
  });

  it("says why an entry cannot be installed, and nothing for one that can", () => {
    const byId = (id: string) => catalogue.entries.find((e) => e.id === id)!;
    expect(whyNotInstallable(byId("notion"))).toMatch(/OAuth/);
    expect(whyNotInstallable(byId("playwright"))).toMatch(/local process/);
    expect(whyNotInstallable(byId("github"))).toBeNull();
  });

  it("writes stars and categories", () => {
    expect(stars(63088)).toBe("63 088");
    expect(categoriesOf(catalogue.entries)).toContain("developer-tools");
  });
});

describe("the catalogue page", () => {
  it("says what it is for: tools agents call, under the connector's key, not how a workflow moves its ticket", async () => {
    page();
    const purpose = await screen.findByRole("complementary", { name: /tools your agents call/ });
    expect(purpose).toHaveTextContent("never hands it to the agent");
    expect(purpose).toHaveTextContent("A workflow does not move its ticket through MCP");
    expect(within(purpose).getByRole("link", { name: /connector types/ })).toHaveAttribute("href", "/catalogue/connectors");
  });

  it("lists the entries, each with its badges, and the facets narrow them", async () => {
    page();
    const github = await screen.findByTestId("entry-github");
    expect(within(github).getByText("Official")).toBeInTheDocument();
    expect(within(github).getByText("Bearer token")).toBeInTheDocument();
    expect(github).toHaveTextContent("stars as of 2026-10-15");
    const total = catalogue.entries.length;
    expect(screen.getByText(`${total} servers`)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Search the catalogue"), { target: { value: "grafana" } });
    expect(screen.getByTestId("entry-grafana")).toBeInTheDocument();
    expect(screen.queryByTestId("entry-github")).toBeNull();
    fireEvent.change(screen.getByLabelText("Search the catalogue"), { target: { value: "" } });

    fireEvent.click(screen.getByLabelText("Installable only"));
    expect(screen.queryByTestId("entry-notion")).toBeNull();
    expect(screen.getByTestId("entry-github")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Search the catalogue"), { target: { value: "no-such-server-anywhere" } });
    expect(screen.getByText("Nothing matches")).toBeInTheDocument();
  });

  it("an OAuth entry and a local process are listed with their reason and a disabled install", async () => {
    page();
    const notion = await screen.findByTestId("entry-notion");
    expect(within(notion).getByRole("button", { name: "Install" })).toBeDisabled();
    expect(notion).toHaveTextContent("Needs OAuth");
    const playwright = screen.getByTestId("entry-playwright");
    expect(within(playwright).getByRole("button", { name: "Install" })).toBeDisabled();
    expect(playwright).toHaveTextContent("Runs as a local process");
    expect(within(screen.getByTestId("entry-github")).getByRole("button", { name: "Install" })).toBeEnabled();
  });

  it("the drawer seals a typed token by default, then discovers and lands on the connector (S39-06e)", async () => {
    install.mockResolvedValue({ name: "gh", kind: "mcp", type: "mcp", operations: [] });
    discover.mockResolvedValue({ added: ["search"], changed: [], removed: [], unchanged: 0 });
    page();
    fireEvent.click(within(await screen.findByTestId("entry-github")).getByRole("button", { name: "Install" }));
    const drawer = screen.getByRole("dialog");
    expect(drawer).toHaveTextContent("Install GitHub");
    const submit = within(drawer).getByRole("button", { name: "Install" });
    expect(submit).toBeDisabled();

    fireEvent.change(within(drawer).getByLabelText("Connector name"), { target: { value: "" } });
    fireEvent.change(within(drawer).getByLabelText("Connector name"), { target: { value: "gh" } });
    // The organisation's own token, typed once: a password field, sealed by the platform, no operator.
    const token = within(drawer).getByLabelText("token value");
    expect(token).toHaveAttribute("type", "password");
    fireEvent.change(token, { target: { value: "ghp_typed_once" } });
    fireEvent.click(submit);

    await waitFor(() => expect(push).toHaveBeenCalled());
    expect(install).toHaveBeenCalledWith("acme", "github", {
      name: "gh",
      secret_refs: {},
      secret_values: { token: "ghp_typed_once" },
      availability: "selected_projects",
    });
    expect(discover).toHaveBeenCalledWith("acme", "gh");
    expect(push.mock.calls[0]![0]).toMatch(/^\/admin\/connectors\?name=gh&discovery=/);
  });

  it("an entry with no credential posts none, and a refusal stays in the drawer", async () => {
    install.mockReset();
    install.mockRejectedValue(new Error("a connector `exa` already exists in acme"));
    page();
    fireEvent.click(within(await screen.findByTestId("entry-exa")).getByRole("button", { name: "Install" }));
    const drawer = screen.getByRole("dialog");
    expect(within(drawer).queryByLabelText("token value")).toBeNull();
    expect(drawer).toHaveTextContent("takes no credential");
    fireEvent.click(within(drawer).getByRole("button", { name: "Install" }));
    expect(await within(drawer).findByText(/already exists/)).toBeInTheDocument();
    expect(install).toHaveBeenCalledWith("acme", "exa", { name: "exa", secret_refs: {}, availability: "selected_projects" });
    expect(push).not.toHaveBeenCalledWith(expect.stringContaining("name=exa"));
  });

  it("Escape closes the drawer", async () => {
    page();
    fireEvent.click(within(await screen.findByTestId("entry-github")).getByRole("button", { name: "Install" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("rights and per-project enablement (ADR 0074)", () => {
  it("an entry the caller cannot install has a disabled button and the server's reason", async () => {
    overrides.set("github", { can: { install: false, configure_projects: [], enable_projects: [] }, cannot_reason: "connectors of acme are installed by its administrator" });
    page();
    const github = await screen.findByTestId("entry-github");
    expect(within(github).getByRole("button", { name: "Install" })).toBeDisabled();
    expect(github).toHaveTextContent("installed by its administrator");
    overrides.clear();
  });

  it("the install form offers selected projects by default and posts every project when chosen", async () => {
    install.mockReset();
    install.mockResolvedValue({ name: "github", kind: "mcp", type: "mcp", operations: [] });
    discover.mockResolvedValue({ added: [], changed: [], removed: [], unchanged: 0 });
    page();
    fireEvent.click(within(await screen.findByTestId("entry-github")).getByRole("button", { name: "Install" }));
    const drawer = screen.getByRole("dialog");
    expect(within(drawer).getByLabelText(/Selected projects/)).toBeChecked();
    fireEvent.click(within(drawer).getByLabelText("Every project"));
    fireEvent.change(within(drawer).getByLabelText("token value"), { target: { value: "ghp_typed_once" } });
    fireEvent.click(within(drawer).getByRole("button", { name: "Install" }));
    await waitFor(() => expect(install).toHaveBeenCalled());
    expect(install.mock.calls[0]![2]).toMatchObject({ availability: "all_projects" });
  });

  it("a reference stays possible for an operator's vault, and a value typed as one is refused", async () => {
    install.mockReset();
    install.mockResolvedValue({ name: "github", kind: "mcp", type: "mcp", operations: [] });
    discover.mockResolvedValue({ added: [], changed: [], removed: [], unchanged: 0 });
    page();
    fireEvent.click(within(await screen.findByTestId("entry-github")).getByRole("button", { name: "Install" }));
    const drawer = screen.getByRole("dialog");
    const submit = within(drawer).getByRole("button", { name: "Install" });
    fireEvent.click(within(drawer).getByRole("button", { name: "Use a reference instead" }));
    fireEvent.change(within(drawer).getByLabelText("token reference"), { target: { value: "ghp_not_a_reference" } });
    expect(within(drawer).getByRole("alert")).toHaveTextContent("A reference is expected");
    expect(submit).toBeDisabled();
    fireEvent.change(within(drawer).getByLabelText("token reference"), { target: { value: "env:CONNECTOR_GITHUB" } });
    fireEvent.click(submit);
    await waitFor(() => expect(install).toHaveBeenCalled());
    expect(install.mock.calls[0]![2]).toEqual({
      name: "github",
      secret_refs: { token: "env:CONNECTOR_GITHUB" },
      availability: "selected_projects",
    });
  });

  it("an installed entry says where it is on, and Projects… toggles a project through the API", async () => {
    enable.mockResolvedValue({});
    overrides.set("github", {
      state: "installed",
      installed_as: ["github"],
      can: { install: true, configure_projects: [], enable_projects: ["billing-api", "checkout-web"] },
      enablement: [{ connector: "github", availability: "selected_projects", enabled_projects: [] }],
    });
    page();
    const github = await screen.findByTestId("entry-github");
    expect(github).toHaveTextContent("on in no project yet");
    fireEvent.click(within(github).getByRole("button", { name: "Projects…" }));
    const drawer = screen.getByRole("dialog");
    fireEvent.click(within(drawer).getByLabelText("billing-api"));
    await waitFor(() => expect(enable).toHaveBeenCalledWith("billing-api", "github"));
    fireEvent.click(within(drawer).getByLabelText("billing-api"));
    await waitFor(() => expect(disable).toHaveBeenCalledWith("billing-api", "github"));
    overrides.clear();
  });

  it("a refusal stays in the drawer, and an entry with no project to enable has no Projects…", async () => {
    enable.mockRejectedValue(new Error("only the owner of billing-api may"));
    overrides.set("github", {
      state: "installed",
      can: { install: true, configure_projects: [], enable_projects: ["billing-api"] },
      enablement: [{ connector: "github", availability: "selected_projects", enabled_projects: [] }],
    });
    overrides.set("exa", {
      state: "installed",
      enablement: [{ connector: "exa", availability: "selected_projects", enabled_projects: [] }],
    });
    page();
    expect(within(await screen.findByTestId("entry-exa")).queryByRole("button", { name: "Projects…" })).toBeNull();
    fireEvent.click(within(screen.getByTestId("entry-github")).getByRole("button", { name: "Projects…" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByLabelText("billing-api"));
    expect(await within(screen.getByRole("dialog")).findByText(/only the owner/)).toBeInTheDocument();
    overrides.clear();
  });

  it("an entry on for every project says so", async () => {
    overrides.set("github", { state: "installed", enablement: [{ connector: "github", availability: "all_projects", enabled_projects: [] }] });
    page();
    expect(await screen.findByTestId("enablement-github")).toHaveTextContent("on for every project");
    overrides.clear();
  });
});
