// SPDX-License-Identifier: Apache-2.0
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import OrgConnectorsPage from "@/app/admin/connectors/page";
import { api, ApiError } from "@/lib/api";
import { deliveryPatch, isOutpostWrite, statementRefusals } from "@/lib/outposts";
import type { ConnectorOperation, OrgConnector } from "@/lib/types";

/**
 * How a write at an outpost is delivered, in the operation's rule (S65-17c, ADR 0149 §6). A write of a
 * connector of type `mcp-outpost` is opened only when its rule says one of two things: that it takes the
 * platform's idempotency key (`platform_arguments: ["action_key"]`), or that an in-doubt outcome is
 * accepted for it, explicitly (`in_doubt_accepted`). The operation editor offers both for such a write and
 * for nothing else, sends them alone or with the policy that opens the write, and shows the API's refusals
 * as it gives them: its sentence, and its words on each statement it names.
 */
vi.mock("next/navigation", () => ({ usePathname: () => "/admin/connectors", useSearchParams: () => new URLSearchParams() }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme" }) }));

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      orgConnectors: vi.fn(),
      projects: vi.fn().mockResolvedValue({ items: [], meta: { has_more: false } }),
      connectors: vi.fn().mockResolvedValue([]),
      connectorTypes: vi.fn().mockResolvedValue([]),
      outposts: vi.fn().mockResolvedValue({ items: [] }),
      updateConnectorOperation: vi.fn(),
    },
  };
});

const KEYED = { type: "object", properties: { title: { type: "string" }, action_key: { type: "string" } } };
const UNKEYED = { type: "object", properties: { title: { type: "string" } } };

function op(name: string, values: Partial<ConnectorOperation> = {}): ConnectorOperation {
  return { name, access: "write", policy: "forbidden", groups: [], input_schema: KEYED, ...values };
}

function atOutpost(operations: ConnectorOperation[]): OrgConnector {
  return {
    name: "tracker-onsite",
    kind: "mcp",
    type: "mcp-outpost",
    config: { outpost: "factory-floor", endpoint: "tracker", timeout_s: 30 },
    operations,
  } as unknown as OrgConnector;
}

const DIRECT = {
  name: "supplier-agent",
  kind: "mcp",
  type: "mcp",
  config: { url: "https://supplier.example/mcp" },
  operations: [op("order_laptop")],
} as unknown as OrgConnector;

/** The 422 the API gives for a write at an outpost left open under neither statement: it names both. */
const NEITHER =
  "tracker-onsite/create_issue is a write of a connector reached through an outpost. Such a write is delivered at most once. It is opened only when its rule says one of two things, and it says neither. The operation stays as it was";
const SAID_NOWHERE = "a write through an outpost says how it is delivered: this, or the other";

function neitherIsSaid(): ApiError {
  return new ApiError(422, {
    title: "Unprocessable entity",
    detail: NEITHER,
    errors: [
      { loc: ["body", "platform_arguments"], msg: SAID_NOWHERE },
      { loc: ["body", "in_doubt_accepted"], msg: SAID_NOWHERE },
    ],
  });
}

function page(connectors: OrgConnector[]) {
  vi.mocked(api.orgConnectors).mockResolvedValue(connectors);
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <OrgConnectorsPage />
    </QueryClientProvider>,
  );
}

async function statements(name: string) {
  return within(await screen.findByTestId(`delivery-${name}`));
}

const KEY = /takes the platform's idempotency key/;
const DOUBT = /in-doubt outcome is accepted for it, explicitly/;

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.updateConnectorOperation).mockImplementation(async (_org, _name, operation) => op(operation));
});

describe("where the two statements are offered", () => {
  it("for a write of a connector at an outpost, and never for its reads or for a direct connector", async () => {
    page([atOutpost([op("search_issues", { access: "read", policy: "allowed" }), op("create_issue")]), DIRECT]);
    const shown = await statements("create_issue");
    expect(shown.getByRole("checkbox", { name: KEY })).toBeInTheDocument();
    expect(shown.getByRole("checkbox", { name: DOUBT })).toBeInTheDocument();
    expect(shown.getByText(/delivered at most once/)).toBeInTheDocument();
    expect(shown.getByText(/silence opens nothing/)).toBeInTheDocument();
    expect(shown.getByTestId("delivery-state-create_issue")).toHaveTextContent("Says neither: it stays forbidden");
    await screen.findByTestId("connector-supplier-agent");
    expect(screen.queryByTestId("delivery-search_issues")).toBeNull();
    expect(screen.queryByTestId("delivery-order_laptop")).toBeNull();
    expect(screen.getAllByRole("checkbox", { name: DOUBT })).toHaveLength(1);
  });

  it("reads the rule as it is stored", async () => {
    page([atOutpost([op("create_issue", { policy: "approval", platform_arguments: ["action_key"], in_doubt_accepted: true })])]);
    const shown = await statements("create_issue");
    expect(shown.getByRole("checkbox", { name: KEY })).toBeChecked();
    expect(shown.getByRole("checkbox", { name: DOUBT })).toBeChecked();
    expect(shown.getByTestId("delivery-state-create_issue")).toHaveTextContent("Both stated");
    expect(shown.getByRole("button", { name: "Set" })).toBeDisabled();
  });

  it("closes the key for a tool whose input schema has no `action_key`, and says why", async () => {
    page([atOutpost([op("create_issue", { input_schema: UNKEYED }), op("close_issue", { input_schema: UNKEYED, platform_arguments: ["action_key"] })])]);
    const closed = await statements("create_issue");
    expect(closed.getByRole("checkbox", { name: KEY })).toBeDisabled();
    expect(closed.getByText(/has no `action_key` argument: the platform cannot hand it one/)).toBeInTheDocument();
    expect(closed.getByRole("checkbox", { name: DOUBT })).toBeEnabled();
    // One already stated can still be withdrawn.
    expect((await statements("close_issue")).getByRole("checkbox", { name: KEY })).toBeEnabled();
  });
});

describe("what the editor sends", () => {
  it("states each one alone with Set", async () => {
    page([atOutpost([op("create_issue")])]);
    const shown = await statements("create_issue");
    fireEvent.click(shown.getByRole("checkbox", { name: DOUBT }));
    fireEvent.click(shown.getByRole("button", { name: "Set" }));
    await waitFor(() => expect(api.updateConnectorOperation).toHaveBeenCalledWith("acme", "tracker-onsite", "create_issue", { in_doubt_accepted: true }));
    fireEvent.click(shown.getByRole("checkbox", { name: DOUBT }));
    fireEvent.click(shown.getByRole("checkbox", { name: KEY }));
    fireEvent.click(shown.getByRole("button", { name: "Set" }));
    await waitFor(() =>
      expect(api.updateConnectorOperation).toHaveBeenLastCalledWith("acme", "tracker-onsite", "create_issue", { platform_arguments: ["action_key"] }),
    );
  });

  it("withdraws them with an empty list and null, and keeps the other platform arguments", async () => {
    page([
      atOutpost([
        op("create_issue", { platform_arguments: ["action_key"], in_doubt_accepted: true }),
        op("update_issue", { platform_arguments: ["action_key", "work_item_key"] }),
      ]),
    ]);
    const both = await statements("create_issue");
    fireEvent.click(both.getByRole("checkbox", { name: KEY }));
    fireEvent.click(both.getByRole("checkbox", { name: DOUBT }));
    fireEvent.click(both.getByRole("button", { name: "Set" }));
    await waitFor(() =>
      expect(api.updateConnectorOperation).toHaveBeenCalledWith("acme", "tracker-onsite", "create_issue", { platform_arguments: [], in_doubt_accepted: null }),
    );
    const keyed = await statements("update_issue");
    fireEvent.click(keyed.getByRole("checkbox", { name: KEY }));
    fireEvent.click(keyed.getByRole("button", { name: "Set" }));
    await waitFor(() =>
      expect(api.updateConnectorOperation).toHaveBeenLastCalledWith("acme", "tracker-onsite", "update_issue", { platform_arguments: ["work_item_key"] }),
    );
  });

  it("opens the write with the statement drafted for it, in the same request as the policy", async () => {
    page([atOutpost([op("create_issue")])]);
    const shown = await statements("create_issue");
    fireEvent.click(shown.getByRole("checkbox", { name: DOUBT }));
    fireEvent.change(screen.getByRole("combobox", { name: "Policy of create_issue" }), { target: { value: "approval" } });
    fireEvent.click(await screen.findByRole("button", { name: "Set approval" }));
    await waitFor(() =>
      expect(api.updateConnectorOperation).toHaveBeenCalledWith("acme", "tracker-onsite", "create_issue", { policy: "approval", in_doubt_accepted: true }),
    );
  });

  it("sends a direct connector's policy alone", async () => {
    page([DIRECT]);
    await screen.findByTestId("connector-supplier-agent");
    fireEvent.change(screen.getByRole("combobox", { name: "Policy of order_laptop" }), { target: { value: "approval" } });
    fireEvent.click(await screen.findByRole("button", { name: "Set approval" }));
    await waitFor(() => expect(api.updateConnectorOperation).toHaveBeenCalledWith("acme", "supplier-agent", "order_laptop", { policy: "approval" }));
  });
});

describe("the API's refusals, as it gives them", () => {
  it("a write opened under neither statement: the confirmation keeps the sentence, each statement says it was named", async () => {
    vi.mocked(api.updateConnectorOperation).mockRejectedValue(neitherIsSaid());
    page([atOutpost([op("create_issue")])]);
    await statements("create_issue");
    fireEvent.change(screen.getByRole("combobox", { name: "Policy of create_issue" }), { target: { value: "allowed" } });
    fireEvent.click(await screen.findByRole("button", { name: "Set allowed" }));
    const confirmation = await screen.findByTestId("confirmation");
    expect(await within(confirmation).findByText(/it says neither/)).toBeInTheDocument();
    expect(api.updateConnectorOperation).toHaveBeenCalledWith("acme", "tracker-onsite", "create_issue", { policy: "allowed" });
    expect(await screen.findByTestId("refused-platform_arguments-create_issue")).toHaveTextContent(SAID_NOWHERE);
    expect(screen.getByTestId("refused-in_doubt_accepted-create_issue")).toHaveTextContent(SAID_NOWHERE);
    expect(screen.getAllByText(/it says neither/)).toHaveLength(1);
  });

  it("withdrawing the last statement of an open write: the sentence and both statements, on the statements", async () => {
    vi.mocked(api.updateConnectorOperation).mockRejectedValue(neitherIsSaid());
    page([atOutpost([op("create_issue", { policy: "approval", in_doubt_accepted: true })])]);
    const shown = await statements("create_issue");
    fireEvent.click(shown.getByRole("checkbox", { name: DOUBT }));
    fireEvent.click(shown.getByRole("button", { name: "Set" }));
    expect(await shown.findByTestId("delivery-refusal-create_issue")).toHaveTextContent(/it says neither/);
    expect(shown.getByTestId("refused-platform_arguments-create_issue")).toHaveTextContent(SAID_NOWHERE);
    expect(shown.getByTestId("refused-in_doubt_accepted-create_issue")).toHaveTextContent(SAID_NOWHERE);
  });

  it("a refusal of one statement is shown on that statement only", async () => {
    const missing = "the operation has no argument `action_key` (its input schema declares: title): the platform cannot hand it one";
    vi.mocked(api.updateConnectorOperation).mockRejectedValue(
      new ApiError(422, { title: "Unprocessable entity", detail: `tracker-onsite/create_issue: ${missing}`, errors: [{ loc: ["body", "platform_arguments"], msg: missing }] }),
    );
    page([atOutpost([op("create_issue")])]);
    const shown = await statements("create_issue");
    fireEvent.click(shown.getByRole("checkbox", { name: KEY }));
    fireEvent.click(shown.getByRole("button", { name: "Set" }));
    expect(await shown.findByTestId("refused-platform_arguments-create_issue")).toHaveTextContent("cannot hand it one");
    expect(shown.queryByTestId("refused-in_doubt_accepted-create_issue")).toBeNull();
    expect(shown.getByTestId("delivery-refusal-create_issue")).toHaveTextContent("tracker-onsite/create_issue:");
  });

  it("a refusal that names no statement stays where the editor's other refusals are", async () => {
    vi.mocked(api.updateConnectorOperation).mockRejectedValue(new ApiError(403, { title: "Forbidden", detail: "insufficient rights" }));
    page([atOutpost([op("create_issue")])]);
    const shown = await statements("create_issue");
    fireEvent.click(shown.getByRole("checkbox", { name: DOUBT }));
    fireEvent.click(shown.getByRole("button", { name: "Set" }));
    expect(await screen.findByText("insufficient rights")).toBeInTheDocument();
    expect(shown.queryByTestId("delivery-refusal-create_issue")).toBeNull();
  });
});

describe("the helpers", () => {
  it("a write at an outpost is a write of an `mcp-outpost` connector", () => {
    const connector = { kind: "mcp", type: "mcp-outpost" };
    expect(isOutpostWrite(connector, { access: "write" })).toBe(true);
    expect(isOutpostWrite(connector, { access: "read" })).toBe(false);
    expect(isOutpostWrite({ kind: "mcp", type: "mcp" }, { access: "write" })).toBe(false);
  });

  it("the patch carries what changed and nothing else", () => {
    expect(deliveryPatch({}, { key: false, accepted: false })).toEqual({});
    expect(deliveryPatch({}, { key: true, accepted: true })).toEqual({ platform_arguments: ["action_key"], in_doubt_accepted: true });
    expect(deliveryPatch({ platform_arguments: ["work_item_key"] }, { key: true, accepted: false })).toEqual({
      platform_arguments: ["action_key", "work_item_key"],
    });
    expect(deliveryPatch({ in_doubt_accepted: true }, { key: false, accepted: false })).toEqual({ in_doubt_accepted: null });
  });

  it("reads the statements a 422 names, and nothing from another status", () => {
    expect(statementRefusals(neitherIsSaid())).toEqual({ platform_arguments: SAID_NOWHERE, in_doubt_accepted: SAID_NOWHERE });
    expect(statementRefusals(new ApiError(409, { errors: [{ loc: ["body", "in_doubt_accepted"], msg: "x" }] }))).toEqual({});
    expect(statementRefusals(new ApiError(422, { errors: [{ loc: ["body", "config", "outpost"], msg: "x" }] }))).toEqual({});
  });
});
