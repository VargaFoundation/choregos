import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import OrgConnectorsPage from "@/app/admin/connectors/page";
import { Connecteurs } from "@/components/connecteurs";
import { inSlotOrder, nextSlot } from "@/components/secret-field";
import type { ConnectorDto, ConnectorType, OrgConnector } from "@/lib/types";

vi.mock("next/navigation", () => ({
  usePathname: () => "/admin/connectors",
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme" }) }));

const VALUE = "client-secret-AAAA-1111";
const WHEN = new Date(Date.now() - 3 * 86_400_000).toISOString();

const { api } = vi.hoisted(() => ({
  api: {
    orgConnectors: vi.fn(),
    createOrgConnector: vi.fn(),
    updateOrgConnector: vi.fn(),
    setOrgConnectorSecret: vi.fn(),
    clearOrgConnectorSecret: vi.fn(),
    connectorTypes: vi.fn(),
    projects: vi.fn(),
    connectors: vi.fn(),
    projectRequirements: vi.fn(),
    putConnector: vi.fn(),
    setConnectorSecret: vi.fn(),
    clearConnectorSecret: vi.fn(),
    testConnector: vi.fn(),
  },
}));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, ...api } };
});

const sharepoint = {
  kind: "documents",
  type: "sharepoint",
  display: "SharePoint",
  config_schema: { type: "object", properties: { tenant_id: { type: "string" } } },
  secret_fields: ["client_secret"],
} as unknown as ConnectorType;
const jira = {
  kind: "tracker",
  type: "jira",
  display: "Jira Cloud",
  config_schema: { type: "object", properties: { base_url: { type: "string" } } },
  secret_fields: ["api_token"],
} as unknown as ConnectorType;

const declared: OrgConnector = {
  name: "diametral-sharepoint",
  kind: "documents",
  type: "sharepoint",
  config: { tenant_id: "acme" },
  secret_refs: { client_secret: "store:connector/diametral-sharepoint/client_secret" },
  secrets: [{ field: "client_secret", source: "store", version: 1, rotated_at: WHEN, rotated_by: "lea@acme.dev" }],
  status: "ok",
  operations: [],
} as unknown as OrgConnector;

function wrap(ui: React.ReactNode) {
  return render(<QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  api.orgConnectors.mockResolvedValue([declared]);
  api.connectorTypes.mockResolvedValue([sharepoint, jira]);
  api.projects.mockResolvedValue({ items: [], meta: { has_more: false } });
  api.createOrgConnector.mockResolvedValue(declared);
  api.updateOrgConnector.mockResolvedValue(declared);
  api.setOrgConnectorSecret.mockResolvedValue(declared);
  api.clearOrgConnectorSecret.mockResolvedValue(declared);
});


// ── several keys for one field (S39-30c, ADR 0089) ──────────────────────────────────────────

const brave = {
  kind: "web_search",
  type: "brave",
  display: "Brave Search API",
  config_schema: { type: "object", properties: {} },
  secret_fields: ["api_key"],
  pooled_fields: ["api_key"],
} as unknown as ConnectorType;

const search: OrgConnector = {
  name: "search",
  kind: "web_search",
  type: "brave",
  config: {},
  secret_refs: { api_key: "store:connector/search/api_key", "api_key.3": "store:connector/search/api_key.3" },
  secrets: [
    { field: "api_key.3", source: "store", version: 1, rotated_at: WHEN, rotated_by: "lea@acme.dev" },
    { field: "api_key", source: "store", version: 2, rotated_at: WHEN, rotated_by: "lea@acme.dev" },
  ],
  status: "ok",
  operations: [],
} as unknown as OrgConnector;

describe("slots of a pooled field", () => {
  it("orders slots by number and finds the first free one", () => {
    expect(inSlotOrder(["api_key.10", "b", "api_key.2", "api_key"])).toEqual(["api_key", "api_key.2", "api_key.10", "b"]);
    expect(nextSlot("api_key", ["api_key", "api_key.3"])).toBe("api_key.2");
    expect(nextSlot("api_key", [])).toBe("api_key");
    const full = ["api_key", ...[2, 3, 4, 5, 6, 7, 8, 9, 10].map((n) => `api_key.${n}`)];
    expect(nextSlot("api_key", full)).toBeNull();
  });
});

describe("admin connectors: a pooled field takes another key (S39-30c)", () => {
  it("lists the keys in slot order, adds one in the first free slot and types it once", async () => {
    api.orgConnectors.mockResolvedValue([search]);
    api.connectorTypes.mockResolvedValue([brave, sharepoint]);
    wrap(<OrgConnectorsPage />);
    const panel = await screen.findByTestId("secrets-search");
    const fields = within(panel).getAllByTestId(/^secret-state-/).map((n) => n.getAttribute("data-testid"));
    expect(fields).toEqual(["secret-state-api_key", "secret-state-api_key.3"]);
    fireEvent.click(within(panel).getByRole("button", { name: "Add a key to api key" }));
    const slot = within(panel).getByTestId("secret-api_key.2");
    expect(within(slot).getByTestId("secret-state-api_key.2")).toHaveTextContent("not set");
    fireEvent.click(within(slot).getByRole("button", { name: "Set" }));
    fireEvent.change(within(slot).getByLabelText("api key.2 value"), { target: { value: VALUE } });
    fireEvent.click(within(slot).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.setOrgConnectorSecret).toHaveBeenCalledWith("acme", "search", "api_key.2", VALUE));
    expect(document.body.innerHTML).not.toContain(VALUE);
  });

  it("offers no second key where the type holds one", async () => {
    wrap(<OrgConnectorsPage />);
    const panel = await screen.findByTestId("secrets-diametral-sharepoint");
    expect(within(panel).queryByRole("button", { name: /Add a key/ })).toBeNull();
  });
});

describe("a project's connector: a pooled field takes another key (S39-30c)", () => {
  const pooledJira = { ...jira, pooled_fields: ["api_token"] } as unknown as ConnectorType;
  const tracker = {
    id: "c1",
    kind: "tracker",
    type: "jira",
    config: { base_url: "https://acme.atlassian.net" },
    secret_refs: { api_token: "store:project/billing-api/tracker/api_token" },
    secrets: [{ field: "api_token", source: "store", version: 3, rotated_at: WHEN, rotated_by: "bob@acme.dev" }],
    status: "ok",
  } as unknown as ConnectorDto;

  beforeEach(() => {
    api.connectorTypes.mockResolvedValue([sharepoint, pooledJira]);
    api.projectRequirements.mockResolvedValue([{ capability: "tracker", reasons: ["a ticket"], default_type: null }]);
    api.connectors.mockResolvedValue([tracker]);
    api.putConnector.mockResolvedValue(tracker);
  });

  it("sends the new slot's value with the form, never a reference", async () => {
    wrap(<Connecteurs slug="billing-api" />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    fireEvent.click(screen.getByRole("button", { name: "Add a key to api token" }));
    fireEvent.change(screen.getByLabelText("api token.2 value"), { target: { value: VALUE } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.putConnector).toHaveBeenCalledTimes(1));
    const body = api.putConnector.mock.calls[0]!.at(-1);
    expect(body.secret_values).toEqual({ "api_token.2": VALUE });
  });
});
