import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ConnectorCataloguePage from "@/app/catalogue/connectors/page";
import { NO_FILTERS, withToggle } from "@/lib/catalogue";
import { filterConnectorEntries } from "@/lib/connector-catalogue";
import type { ConnectorCatalogue, ConnectorCatalogueEntry } from "@/lib/types";

const { state } = vi.hoisted(() => ({ state: { catalogue: null as unknown } }));

vi.mock("next/navigation", () => ({ usePathname: () => "/catalogue/connectors" }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme" }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: { ...real.api, connectorCatalogue: vi.fn(async () => structuredClone(state.catalogue) as ConnectorCatalogue) },
  };
});

const entry = (kind: string, type: string, display: string, family: string, scope: "organisation" | "project", extra: Partial<ConnectorCatalogueEntry> = {}): ConnectorCatalogueEntry => ({
  kind,
  type,
  display,
  family,
  scope,
  capabilities: [kind],
  secret_fields: [],
  operations: 0,
  installed_as: [],
  configured_in: [],
  enablement: [],
  can: { install: false, configure_projects: [], enable_projects: [] },
  cannot_reason: null,
  ...extra,
});

const ADMIN: ConnectorCatalogue = {
  entries: [
    entry("scm", "github", "GitHub", "Source code", "project", {
      configured_in: ["billing-api"],
      can: { install: false, configure_projects: ["billing-api"], enable_projects: [] },
    }),
    entry("tracker", "jira", "Jira Cloud", "Work trackers", "project", { secret_fields: ["api_token", "webhook_secret"] }),
    entry("identity", "entra", "Microsoft Entra ID", "Directory", "organisation", {
      operations: 6,
      secret_fields: ["client_secret"],
      installed_as: ["entra-acme"],
      enablement: [{ connector: "entra-acme", availability: "selected_projects", enabled_projects: [] }],
      can: { install: true, configure_projects: [], enable_projects: ["billing-api"] },
    }),
  ],
};

function page(catalogue: ConnectorCatalogue) {
  state.catalogue = catalogue;
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ConnectorCataloguePage />
    </QueryClientProvider>,
  );
}

describe("the connector catalogue page (ADR 0074)", () => {
  it("says what it is for: the platform's own API integrations, under the connector's account, not the agents' tools", async () => {
    page(ADMIN);
    const purpose = await screen.findByRole("complementary", { name: /the platform's own integrations/ });
    expect(purpose).toHaveTextContent("Choregos calls these systems through their APIs");
    expect(purpose).toHaveTextContent("never under the person who started the workflow");
    expect(within(purpose).getByRole("link", { name: /MCP servers/ })).toHaveAttribute("href", "/catalogue/mcp-servers");
  });

  it("groups the cards by family, in the server's order, each with its icon and a scope badge", async () => {
    page(ADMIN);
    await screen.findByTestId("entry-scm-github");
    const headings = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(headings).toEqual(["Source code", "Work trackers", "Directory"]);
    const github = screen.getByTestId("entry-scm-github");
    expect(github.querySelector('[data-icon="brand:github"]')).not.toBeNull();
    expect(within(github).getByText("Project")).toBeInTheDocument();
    expect(github).toHaveTextContent("scm/github");
    expect(github).toHaveTextContent("Configured in billing-api");
    const entra = screen.getByTestId("entry-identity-entra");
    expect(within(entra).getByText("Organisation")).toBeInTheDocument();
    expect(entra).toHaveTextContent("6 operations");
    expect(entra).toHaveTextContent("Needs 1 secret");
    expect(screen.getByTestId("entry-tracker-jira")).toHaveTextContent("Needs 2 secrets");
    expect(entra).toHaveTextContent("on in no project yet");
  });

  it("an administrator declares an organisation connector from a link that preselects its type", async () => {
    page(ADMIN);
    const entra = await screen.findByTestId("entry-identity-entra");
    expect(within(entra).getByRole("link", { name: "Declare" })).toHaveAttribute("href", "/admin/connectors?type=identity/entra");
    expect(within(entra).getByRole("button", { name: "Projects…" })).toBeInTheDocument();
  });

  it("a project owner configures in their project and is told why for the rest", async () => {
    const owner: ConnectorCatalogue = {
      entries: [
        ADMIN.entries[0]!,
        entry("identity", "entra", "Microsoft Entra ID", "Directory", "organisation", {
          cannot_reason: "connectors of acme are installed by its administrator",
        }),
        entry("tracker", "jira", "Jira Cloud", "Work trackers", "project", {
          cannot_reason: "only the owner of a project or an administrator configures its connectors",
        }),
      ],
    };
    page(owner);
    const github = await screen.findByTestId("entry-scm-github");
    expect(within(github).getByRole("link", { name: /Configure in\s+billing-api/ })).toHaveAttribute("href", "/p/billing-api/settings");
    const entra = screen.getByTestId("entry-identity-entra");
    expect(within(entra).queryByRole("link", { name: "Declare" })).toBeNull();
    expect(entra).toHaveTextContent("installed by its administrator");
    expect(screen.getByTestId("entry-tracker-jira")).toHaveTextContent("configures its connectors");
  });

  it("the family facet narrows the grid, and the scope toggle keeps one scope", async () => {
    page(ADMIN);
    await screen.findByTestId("entry-scm-github");
    fireEvent.change(screen.getByLabelText("Category"), { target: { value: "Directory" } });
    expect(screen.queryByTestId("entry-scm-github")).toBeNull();
    expect(screen.getByTestId("entry-identity-entra")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Category"), { target: { value: "" } });
    fireEvent.click(screen.getByLabelText("Project connectors"));
    expect(screen.queryByTestId("entry-identity-entra")).toBeNull();
    expect(screen.getByTestId("entry-scm-github")).toBeInTheDocument();
  });

  it("Projects… opens the enablement drawer of the connector", async () => {
    page(ADMIN);
    fireEvent.click(within(await screen.findByTestId("entry-identity-entra")).getByRole("button", { name: "Projects…" }));
    const drawer = screen.getByRole("dialog");
    expect(drawer).toHaveTextContent("Projects for entra-acme");
    expect(within(drawer).getByLabelText("billing-api")).not.toBeChecked();
  });
});

describe("filterConnectorEntries", () => {
  it("searches display, kind, type and family; both scope toggles on means every scope", () => {
    const all = ADMIN.entries;
    expect(filterConnectorEntries(all, { ...NO_FILTERS, q: "entra" }).map((e) => e.type)).toEqual(["entra"]);
    expect(filterConnectorEntries(all, { ...NO_FILTERS, q: "work trackers" }).map((e) => e.type)).toEqual(["jira"]);
    const both = withToggle(withToggle(NO_FILTERS, "organisation", true), "project", true);
    expect(filterConnectorEntries(all, both)).toHaveLength(3);
    expect(filterConnectorEntries(all, withToggle(NO_FILTERS, "organisation", true)).map((e) => e.type)).toEqual(["entra"]);
  });
});
