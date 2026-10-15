import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import McpCataloguePage from "@/app/admin/connectors/catalogue/page";
import { categoriesOf, matchesText, NO_FILTERS, stars, withToggle } from "@/lib/catalogue";
import { filterMcpEntries, suggestedReference, whyNotInstallable } from "@/lib/mcp-catalogue";
import type { McpCatalogue } from "@/lib/types";
import file from "../src/mocks/mcp-catalogue.json";

const { push, install, discover } = vi.hoisted(() => ({
  push: vi.fn(),
  install: vi.fn(),
  discover: vi.fn(),
}));

vi.mock("next/navigation", () => ({ usePathname: () => "/admin/connectors/catalogue", useRouter: () => ({ push }) }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme" }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      mcpCatalogue: vi.fn(async () => structuredClone(file) as unknown as McpCatalogue),
      installMcpCatalogueEntry: install,
      discoverConnectorOperations: discover,
    },
  };
});

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

  it("writes stars and categories, and proposes a reference only the platform's variables can carry", () => {
    expect(stars(63088)).toBe("63 088");
    expect(categoriesOf(catalogue.entries)).toContain("developer-tools");
    expect(suggestedReference("brave-search")).toBe("env:CONNECTOR_BRAVE_SEARCH");
  });
});

describe("the catalogue page", () => {
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

  it("the drawer posts the name and the secret by reference, then discovers and lands on the connector", async () => {
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
    fireEvent.change(within(drawer).getByLabelText("Secret, by reference"), { target: { value: "ghp_not_a_reference" } });
    expect(within(drawer).getByRole("alert")).toHaveTextContent("A reference is expected");
    expect(submit).toBeDisabled();
    fireEvent.change(within(drawer).getByLabelText("Secret, by reference"), { target: { value: "" } });
    fireEvent.change(within(drawer).getByLabelText("Secret, by reference"), { target: { value: "env:CONNECTOR_GITHUB" } });
    fireEvent.click(submit);

    await waitFor(() => expect(push).toHaveBeenCalled());
    expect(install).toHaveBeenCalledWith("acme", "github", { name: "gh", secret_refs: { token: "env:CONNECTOR_GITHUB" } });
    expect(discover).toHaveBeenCalledWith("acme", "gh");
    expect(push.mock.calls[0]![0]).toMatch(/^\/admin\/connectors\?name=gh&discovery=/);
  });

  it("an entry with no credential posts none, and a refusal stays in the drawer", async () => {
    install.mockReset();
    install.mockRejectedValue(new Error("a connector `exa` already exists in acme"));
    page();
    fireEvent.click(within(await screen.findByTestId("entry-exa")).getByRole("button", { name: "Install" }));
    const drawer = screen.getByRole("dialog");
    expect(within(drawer).queryByLabelText("Secret, by reference")).toBeNull();
    expect(drawer).toHaveTextContent("takes no credential");
    fireEvent.click(within(drawer).getByRole("button", { name: "Install" }));
    expect(await within(drawer).findByText(/already exists/)).toBeInTheDocument();
    expect(install).toHaveBeenCalledWith("acme", "exa", { name: "exa", secret_refs: {} });
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
