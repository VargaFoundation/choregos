// SPDX-License-Identifier: Apache-2.0
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import OrgConnectorsPage from "@/app/admin/connectors/page";
import { api, ApiError } from "@/lib/api";
import type { ConnectorType, OrgConnector, Outpost } from "@/lib/types";

/**
 * The form of a connector at an outpost (S65-17b, ADR 0149 §1): the outpost is chosen among the
 * organisation's, the alias among what that outpost's instances announce (typed only while none
 * announces one), a timeout within its bounds, and no URL or secret field. The declaration's refusals,
 * and discovery's, are shown as the API gives them.
 */
vi.mock("next/navigation", () => ({ usePathname: () => "/admin/connectors", useSearchParams: () => new URLSearchParams() }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme" }) }));

const { outpostType, atTheFloor, outpost } = vi.hoisted(() => ({
  // The type as `GET /connectors/types` serves it: the contract's closed `McpOutpostConnectorConfig`.
  outpostType: {
    kind: "mcp",
    type: "mcp-outpost",
    display: "MCP server at an outpost",
    config_schema: {
      type: "object",
      additionalProperties: false,
      required: ["outpost", "endpoint"],
      properties: {
        outpost: { type: "string", pattern: "^[a-z0-9][a-z0-9-]{0,62}$", description: "an outpost of the same organisation" },
        endpoint: { type: "string", pattern: "^[a-z][a-z0-9-]{0,62}$", description: "the alias of an endpoint in that outpost's policy file" },
        timeout_s: { type: "number", minimum: 1, maximum: 120, default: 30, description: "seconds before a call is abandoned" },
      },
    },
    capabilities: ["mcp"],
    secret_fields: [],
  } as unknown as ConnectorType,
  atTheFloor: {
    name: "tracker-onsite",
    kind: "mcp",
    type: "mcp-outpost",
    config: { outpost: "factory-floor", endpoint: "tracker", timeout_s: 30 },
    operations: [],
  } as unknown as OrgConnector,
  outpost: (slug: string, values: Partial<Outpost> = {}): Outpost => ({
    slug,
    display_name: slug,
    status: "active",
    capabilities: ["tools"],
    epoch: 1,
    online: false,
    last_seen_at: null,
    instances: { pending: 0, active: 0, revoked: 0 },
    endpoints: [],
    connectors: [],
    control_plane: null,
    door_keys: [],
    created_by: null,
    created_at: "2026-11-01T00:00:00Z",
    ...values,
  }),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      orgConnectors: vi.fn(),
      projects: vi.fn().mockResolvedValue({ items: [], meta: { has_more: false } }),
      connectors: vi.fn().mockResolvedValue([]),
      connectorTypes: vi.fn(),
      outposts: vi.fn(),
      createOrgConnector: vi.fn(),
      updateOrgConnector: vi.fn(),
      discoverConnectorOperations: vi.fn(),
    },
  };
});

const OUTPOSTS = [
  outpost("factory-floor", { display_name: "Factory floor", online: true, endpoints: ["tracker", "warehouse"], instances: { pending: 0, active: 1, revoked: 0 } }),
  outpost("branch-office", { display_name: "Branch office" }),
  outpost("cold-store", { status: "paused", endpoints: ["sensors"] }),
  outpost("old-site", { status: "revoked" }),
];

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.orgConnectors).mockResolvedValue([atTheFloor]);
  vi.mocked(api.connectorTypes).mockResolvedValue([outpostType]);
  vi.mocked(api.outposts).mockResolvedValue({ items: OUTPOSTS, enabled: true, door_signing_key: true });
  vi.mocked(api.createOrgConnector).mockResolvedValue(atTheFloor);
});

function page() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <OrgConnectorsPage />
    </QueryClientProvider>,
  );
}

/** The declaration form, the type chosen: what an administrator sees. */
async function declaring() {
  page();
  await screen.findByTestId("connector-tracker-onsite");
  const form = screen.getByRole("button", { name: "Declare" }).closest("form")!;
  fireEvent.change(within(form).getByPlaceholderText("entra-acme"), { target: { value: "tracker-plant" } });
  fireEvent.change(within(form).getByRole("combobox", { name: "Type" }), { target: { value: "mcp/mcp-outpost" } });
  await within(form).findByTestId("outpost-connector-fields");
  return within(form);
}

const options = (select: HTMLElement) => within(select).getAllByRole("option").map((o) => ({ text: o.textContent, disabled: (o as HTMLOptionElement).disabled }));

describe("declaring a connector at an outpost", () => {
  it("offers the organisation's outposts as choices, never a text box, and says which cannot serve", async () => {
    const form = await declaring();
    expect(api.outposts).toHaveBeenCalledWith("acme");
    const choice = form.getByRole("combobox", { name: "Outpost" });
    expect(choice.tagName).toBe("SELECT");
    expect(options(choice)).toEqual([
      { text: "—", disabled: false },
      { text: "Factory floor (factory-floor) — online", disabled: false },
      { text: "Branch office (branch-office) — never seen", disabled: false },
      { text: "cold-store — paused, never seen", disabled: false },
      { text: "old-site — revoked", disabled: true },
    ]);
  });

  it("offers the aliases the chosen outpost's instances announce, and sends the three keys", async () => {
    const form = await declaring();
    fireEvent.change(form.getByRole("combobox", { name: "Outpost" }), { target: { value: "factory-floor" } });
    const alias = form.getByRole("combobox", { name: "Endpoint" });
    expect(options(alias).map((o) => o.text)).toEqual(["—", "tracker", "warehouse"]);
    expect(form.getByRole("button", { name: "Declare" })).toBeDisabled();
    fireEvent.change(alias, { target: { value: "warehouse" } });
    fireEvent.change(form.getByLabelText("Timeout (seconds)"), { target: { value: "45" } });
    fireEvent.click(form.getByRole("button", { name: "Declare" }));
    await waitFor(() => expect(api.createOrgConnector).toHaveBeenCalled());
    expect(vi.mocked(api.createOrgConnector).mock.calls[0]![1]).toEqual({
      name: "tracker-plant",
      type: "mcp-outpost",
      kind: "mcp",
      config: { outpost: "factory-floor", endpoint: "warehouse", timeout_s: 45 },
    });
  });

  it("lets the alias be typed only while the outpost announces none, and says so", async () => {
    const form = await declaring();
    const alias = () => form.getByLabelText("Endpoint");
    expect(alias().tagName).toBe("INPUT");
    expect(alias()).toBeDisabled();
    fireEvent.change(form.getByRole("combobox", { name: "Outpost" }), { target: { value: "branch-office" } });
    expect(alias().tagName).toBe("INPUT");
    expect(alias()).toBeEnabled();
    expect(form.getByText(/No instance of branch-office announces an endpoint yet: type the alias/)).toBeInTheDocument();
    fireEvent.change(alias(), { target: { value: "https://erp.internal.example.com" } });
    expect(form.getByText(/An alias, never an address/)).toBeInTheDocument();
    expect(form.getByRole("button", { name: "Declare" })).toBeDisabled();
    fireEvent.change(alias(), { target: { value: "erp" } });
    expect(form.getByRole("button", { name: "Declare" })).toBeEnabled();
    // Back to an outpost that announces aliases: a choice again, and the typed one is not kept.
    fireEvent.change(form.getByRole("combobox", { name: "Outpost" }), { target: { value: "factory-floor" } });
    expect(form.getByRole("combobox", { name: "Endpoint" })).toHaveValue("");
  });

  it("bounds the timeout as the type declares it", async () => {
    const form = await declaring();
    fireEvent.change(form.getByRole("combobox", { name: "Outpost" }), { target: { value: "factory-floor" } });
    fireEvent.change(form.getByRole("combobox", { name: "Endpoint" }), { target: { value: "tracker" } });
    const timeout = form.getByLabelText("Timeout (seconds)");
    expect([timeout.getAttribute("min"), timeout.getAttribute("max"), timeout.getAttribute("placeholder")]).toEqual(["1", "120", "30"]);
    fireEvent.change(timeout, { target: { value: "121" } });
    expect(form.getByText("A number of seconds between 1 and 120.")).toBeInTheDocument();
    expect(form.getByRole("button", { name: "Declare" })).toBeDisabled();
  });

  it("shows no URL and no secret field: the address and the credential are the outpost's", async () => {
    const form = await declaring();
    fireEvent.change(form.getByRole("combobox", { name: "Outpost" }), { target: { value: "factory-floor" } });
    const fields = form.getByTestId("outpost-connector-fields");
    expect(within(fields).getAllByRole("combobox").map((c) => c.getAttribute("id") && document.querySelector(`label[for="${c.id}"]`)?.textContent)).toEqual([
      "Outpost",
      "Endpoint",
    ]);
    expect(within(fields).getAllByRole("spinbutton")).toHaveLength(1);
    expect(within(fields).queryAllByRole("textbox")).toHaveLength(0);
    expect(form.queryByLabelText(/url/i)).toBeNull();
    expect(form.queryByLabelText(/token|secret|password|key/i)).toBeNull();
    expect(screen.queryAllByTestId(/^secret-/)).toHaveLength(0);
    expect(fields).toHaveTextContent("No URL and no secret");
  });

  it("shows the 422 on the field it names", async () => {
    vi.mocked(api.createOrgConnector).mockRejectedValue(
      new ApiError(422, {
        title: "Unprocessable entity",
        detail: "`branch-office` is not an outpost of acme: an administrator creates it first",
        errors: [{ loc: ["body", "config", "outpost"], msg: "`branch-office` is not an outpost of acme: an administrator creates it first" }],
      }),
    );
    const form = await declaring();
    fireEvent.change(form.getByRole("combobox", { name: "Outpost" }), { target: { value: "branch-office" } });
    fireEvent.change(form.getByLabelText("Endpoint"), { target: { value: "erp" } });
    fireEvent.click(form.getByRole("button", { name: "Declare" }));
    const choice = form.getByRole("combobox", { name: "Outpost" });
    await waitFor(() => expect(choice).toHaveAttribute("aria-invalid", "true"));
    const message = document.getElementById(choice.getAttribute("aria-describedby")!)!;
    expect(message).toHaveTextContent("`branch-office` is not an outpost of acme: an administrator creates it first");
    expect(form.getByLabelText("Endpoint")).not.toHaveAttribute("aria-invalid");
    // Changing the configuration clears what the API said of the previous one.
    fireEvent.change(choice, { target: { value: "factory-floor" } });
    expect(choice).not.toHaveAttribute("aria-invalid");
  });

  it("shows a 409 with its code, the outpost, the alias and the API's own words", async () => {
    vi.mocked(api.createOrgConnector).mockRejectedValue(
      new ApiError(409, {
        title: "Conflict",
        detail: "the outpost `cold-store` is paused: it is handed nothing (an administrator resumes it)",
        code: "outpost_paused",
        outpost: "cold-store",
        endpoint: "sensors",
      }),
    );
    const form = await declaring();
    fireEvent.change(form.getByRole("combobox", { name: "Outpost" }), { target: { value: "cold-store" } });
    fireEvent.change(form.getByRole("combobox", { name: "Endpoint" }), { target: { value: "sensors" } });
    fireEvent.click(form.getByRole("button", { name: "Declare" }));
    const refusal = await form.findByTestId("outpost-route-refusal");
    expect(refusal).toHaveTextContent("The outpost is handed nothing");
    expect(within(refusal).getByTestId("refusal-code")).toHaveTextContent("outpost_paused");
    expect(refusal).toHaveTextContent("Outpost cold-store");
    expect(refusal).toHaveTextContent("Alias sensors");
    expect(refusal).toHaveTextContent("the outpost cold-store is paused: it is handed nothing (an administrator resumes it)");
  });
});

describe("discovering a connector at an outpost", () => {
  async function discover(problem: ConstructorParameters<typeof ApiError>[1], status = 409) {
    vi.mocked(api.discoverConnectorOperations).mockRejectedValue(new ApiError(status, problem));
    page();
    const card = await screen.findByTestId("connector-tracker-onsite");
    expect(within(card).getByTestId("route-tracker-onsite")).toHaveTextContent("at outpost: factory-floor/tracker");
    fireEvent.click(within(card).getByRole("button", { name: "Discover" }));
    return within(await within(card).findByTestId("outpost-route-refusal"));
  }

  it("says the outpost is offline, in its words", async () => {
    const refusal = await discover({
      title: "Conflict",
      detail: "the outpost `factory-floor` is offline: no instance of it polled in the last 70 seconds, and nothing reached its server",
      code: "outpost_offline",
      outpost: "factory-floor",
      endpoint: "tracker",
    });
    expect(refusal.getByText("The outpost is offline")).toBeInTheDocument();
    expect(refusal.getByTestId("refusal-code")).toHaveTextContent("outpost_offline");
    expect(refusal.getByRole("alert")).toHaveTextContent("no instance of it polled in the last 70 seconds");
  });

  it("names the outpost's own code when its policy refused", async () => {
    const refusal = await discover({
      title: "Conflict",
      detail: "the outpost `factory-floor` refused the call: unknown_endpoint",
      code: "outpost_refused",
      outpost: "factory-floor",
      endpoint: "tracker",
      refusal: "unknown_endpoint",
    });
    expect(refusal.getByText("The outpost refused the call")).toBeInTheDocument();
    expect(refusal.getByTestId("outpost-own-code")).toHaveTextContent("unknown_endpoint");
  });

  it("says a claimed listing got no answer in time", async () => {
    const refusal = await discover({
      title: "Conflict",
      detail: "the outpost `factory-floor` did not answer in time",
      code: "outpost_timeout",
      outpost: "factory-floor",
      endpoint: "tracker",
    });
    expect(refusal.getByText("The outpost did not answer in time")).toBeInTheDocument();
  });

  it("says when to try again after the outposts' quota refused it", async () => {
    const refusal = await discover(
      { title: "Too many calls to an outpost", detail: "the outpost `factory-floor` has as many calls in flight as the deployment admits", retry_after: 60 },
      429,
    );
    expect(refusal.getByText("Too many calls to the outpost")).toBeInTheDocument();
    expect(refusal.getByTestId("retry-after")).toHaveTextContent("Try again in 1 minute.");
  });
});

describe("editing a connector at an outpost", () => {
  it("is the same form, judged as a declaration", async () => {
    vi.mocked(api.updateOrgConnector).mockRejectedValue(
      new ApiError(409, { title: "Conflict", detail: "the outpost `cold-store` is paused", code: "outpost_paused", outpost: "cold-store", endpoint: "sensors" }),
    );
    page();
    const card = await screen.findByTestId("connector-tracker-onsite");
    fireEvent.click(within(card).getByRole("button", { name: "Edit settings" }));
    const fields = await within(card).findByTestId("outpost-connector-fields");
    const choice = within(fields).getByRole("combobox", { name: "Outpost" });
    expect(choice).toHaveValue("factory-floor");
    expect(within(fields).getByRole("combobox", { name: "Endpoint" })).toHaveValue("tracker");
    fireEvent.change(choice, { target: { value: "cold-store" } });
    fireEvent.change(within(fields).getByRole("combobox", { name: "Endpoint" }), { target: { value: "sensors" } });
    fireEvent.click(within(card).getByRole("button", { name: "Save settings" }));
    expect(await within(card).findByTestId("outpost-route-refusal")).toHaveTextContent("outpost_paused");
    expect(vi.mocked(api.updateOrgConnector).mock.calls[0]![2]).toEqual({ config: { outpost: "cold-store", endpoint: "sensors", timeout_s: 30 } });
  });

  it("keeps a configuration that names an outpost or an alias the organisation no longer offers, and says so", async () => {
    vi.mocked(api.orgConnectors).mockResolvedValue([{ ...atTheFloor, config: { outpost: "gone", endpoint: "tracker" } } as OrgConnector]);
    page();
    const card = await screen.findByTestId("connector-tracker-onsite");
    fireEvent.click(within(card).getByRole("button", { name: "Edit settings" }));
    const fields = await within(card).findByTestId("outpost-connector-fields");
    const choice = within(fields).getByRole("combobox", { name: "Outpost" });
    expect(choice).toHaveValue("gone");
    expect(options(choice).at(-1)!.text).toBe("gone — not an outpost of acme");
  });
});

describe("before any outpost", () => {
  it("leads to Outposts instead of a field to type a name in", async () => {
    vi.mocked(api.outposts).mockResolvedValue({ items: [], enabled: true, door_signing_key: true });
    page();
    await screen.findByTestId("connector-tracker-onsite");
    const form = screen.getByRole("button", { name: "Declare" }).closest("form")!;
    fireEvent.change(within(form).getByRole("combobox", { name: "Type" }), { target: { value: "mcp/mcp-outpost" } });
    expect(await within(form).findByText("No outpost yet")).toBeInTheDocument();
    expect(within(form).getByRole("link", { name: "Create one in Outposts" })).toHaveAttribute("href", "/admin/outposts");
  });

  it("says why when the deployment keeps outposts switched off", async () => {
    vi.mocked(api.outposts).mockRejectedValue(new ApiError(409, { title: "Conflict", detail: "outposts are switched off on this deployment", code: "outposts_disabled" }));
    page();
    await screen.findByTestId("connector-tracker-onsite");
    const form = screen.getByRole("button", { name: "Declare" }).closest("form")!;
    fireEvent.change(within(form).getByRole("combobox", { name: "Type" }), { target: { value: "mcp/mcp-outpost" } });
    expect(await within(form).findByTestId("outposts-deployment-refusal")).toHaveTextContent("Outposts are switched off on this deployment");
  });
});
