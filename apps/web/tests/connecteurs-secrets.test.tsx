import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import OrgConnectorsPage from "@/app/admin/connectors/page";
import { Connecteurs } from "@/components/connecteurs";
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

describe("admin connectors: secrets are typed once (S39-06d)", () => {
  it("declares a connector with the typed value as a secret VALUE, not a reference", async () => {
    api.orgConnectors.mockResolvedValue([]);
    wrap(<OrgConnectorsPage />);
    await screen.findByRole("option", { name: /SharePoint/ });
    fireEvent.change(screen.getByPlaceholderText("entra-acme"), { target: { value: "diametral-sharepoint" } });
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "documents/sharepoint" } });
    const input = await screen.findByLabelText("client secret value");
    expect(input).toHaveAttribute("type", "password");
    fireEvent.change(input, { target: { value: VALUE } });
    fireEvent.click(screen.getByRole("button", { name: "Declare" }));
    await waitFor(() => expect(api.createOrgConnector).toHaveBeenCalledTimes(1));
    const [, body] = api.createOrgConnector.mock.calls[0]!;
    expect(body.secret_values).toEqual({ client_secret: VALUE });
    expect(body.secret_refs).toEqual({});
    // And the form is emptied: the value does not stay in the page.
    await waitFor(() => expect(document.body.innerHTML).not.toContain(VALUE));
  });

  it("can still declare by reference, for an operator who prefers a vault", async () => {
    api.orgConnectors.mockResolvedValue([]);
    wrap(<OrgConnectorsPage />);
    await screen.findByRole("option", { name: /SharePoint/ });
    fireEvent.change(screen.getByPlaceholderText("entra-acme"), { target: { value: "sp" } });
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "documents/sharepoint" } });
    fireEvent.click(await screen.findByRole("button", { name: "Use a reference instead" }));
    fireEvent.change(screen.getByLabelText("client secret reference"), { target: { value: "env:KB_SECRET" } });
    fireEvent.click(screen.getByRole("button", { name: "Declare" }));
    await waitFor(() => expect(api.createOrgConnector).toHaveBeenCalledTimes(1));
    const [, body] = api.createOrgConnector.mock.calls[0]!;
    expect(body.secret_refs).toEqual({ client_secret: "env:KB_SECRET" });
    expect(body.secret_values).toEqual({});
  });

  it("shows a declared connector's secret as set and rotated, and replaces it", async () => {
    wrap(<OrgConnectorsPage />);
    const panel = await screen.findByTestId("secrets-diametral-sharepoint");
    expect(within(panel).getByTestId("secret-state-client_secret")).toHaveTextContent(
      "set · rotated 3 days ago by lea@acme.dev",
    );
    fireEvent.click(within(panel).getByRole("button", { name: "Replace" }));
    fireEvent.change(within(panel).getByLabelText("client secret value"), { target: { value: VALUE } });
    fireEvent.click(within(panel).getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(api.setOrgConnectorSecret).toHaveBeenCalledWith("acme", "diametral-sharepoint", "client_secret", VALUE),
    );
    expect(document.body.innerHTML).not.toContain(VALUE);
  });

  it("clears a secret, and edits the settings without touching it", async () => {
    wrap(<OrgConnectorsPage />);
    const panel = await screen.findByTestId("secrets-diametral-sharepoint");
    fireEvent.click(within(panel).getByRole("button", { name: "Clear" }));
    await waitFor(() =>
      expect(api.clearOrgConnectorSecret).toHaveBeenCalledWith("acme", "diametral-sharepoint", "client_secret"),
    );
    fireEvent.click(within(panel).getByRole("button", { name: "Edit settings" }));
    fireEvent.click(within(panel).getByRole("button", { name: "Save settings" }));
    await waitFor(() =>
      expect(api.updateOrgConnector).toHaveBeenCalledWith("acme", "diametral-sharepoint", { config: { tenant_id: "acme" } }),
    );
  });
});

describe("a project's connectors: the same field (S39-06d)", () => {
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
    api.projectRequirements.mockResolvedValue([{ capability: "tracker", reasons: ["a ticket"], default_type: null }]);
    api.connectors.mockResolvedValue([tracker]);
    api.setConnectorSecret.mockResolvedValue(tracker);
    api.putConnector.mockResolvedValue(tracker);
  });

  it("shows the set secret and replaces it by its own call", async () => {
    wrap(<Connecteurs slug="billing-api" />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    expect(screen.getByTestId("secret-state-api_token")).toHaveTextContent("set · rotated 3 days ago by bob@acme.dev");
    const field = screen.getByTestId("secret-api_token");
    fireEvent.click(within(field).getByRole("button", { name: "Replace" }));
    fireEvent.change(within(field).getByLabelText("api token value"), { target: { value: VALUE } });
    fireEvent.click(within(field).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.setConnectorSecret).toHaveBeenCalledWith("billing-api", "tracker", "api_token", VALUE));
    expect(api.putConnector).not.toHaveBeenCalled();
    expect(document.body.innerHTML).not.toContain(VALUE);
  });
});
