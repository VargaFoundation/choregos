import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import OrgConnectorsPage from "@/app/admin/connectors/page";
import { dataHandlingProblems } from "@/lib/data-handling";
import type { ConnectorType, OrgConnector } from "@/lib/types";

vi.mock("next/navigation", () => ({
  usePathname: () => "/admin/connectors",
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme" }) }));
// A backend that declares something, to test the widening (the shipped vendors all declare "unknown";
// a Python test keeps this table equal to the backends' own).
vi.mock("@/lib/web-search-declared.json", () => ({
  default: {
    brave: { query_retention_days: null, query_reuse: null, region: null, terms_ref: null },
    strict: { query_retention_days: 30, query_reuse: false, region: "eu", terms_ref: "https://x.example/terms" },
  },
}));

const { api } = vi.hoisted(() => ({
  api: {
    orgConnectors: vi.fn(),
    createOrgConnector: vi.fn(),
    updateOrgConnector: vi.fn(),
    connectorTypes: vi.fn(),
    projects: vi.fn(),
    connectors: vi.fn(),
    projectRequirements: vi.fn(),
  },
}));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, ...api } };
});

const dataHandling = {
  type: "object",
  additionalProperties: false,
  properties: {
    query_retention_days: { type: "integer", minimum: 0 },
    query_reuse: { type: "boolean" },
    region: { type: "string" },
    terms_ref: { type: "string" },
  },
};
const searchType = (type: string) =>
  ({
    kind: "web_search",
    type,
    display: `Search ${type}`,
    config_schema: { type: "object", properties: { timeout_s: { type: "number" }, data_handling: dataHandling } },
    secret_fields: ["api_key"],
  }) as unknown as ConnectorType;

const installed = (type: string, config: Record<string, unknown> = {}) =>
  ({
    name: `search-${type}`,
    kind: "web_search",
    type,
    config,
    secret_refs: {},
    secrets: [],
    status: "ok",
    operations: [],
  }) as unknown as OrgConnector;

function wrap(ui: React.ReactNode) {
  return render(<QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  api.orgConnectors.mockResolvedValue([]);
  api.connectorTypes.mockResolvedValue([searchType("brave"), searchType("strict")]);
  api.projects.mockResolvedValue({ items: [], meta: { has_more: false } });
  api.createOrgConnector.mockResolvedValue(installed("brave"));
  api.updateOrgConnector.mockResolvedValue(installed("brave"));
});

async function openDeclareForm(type: string) {
  wrap(<OrgConnectorsPage />);
  await screen.findByRole("option", { name: new RegExp(`Search ${type}`) });
  fireEvent.change(screen.getByPlaceholderText("entra-acme"), { target: { value: "search" } });
  fireEvent.change(screen.getByRole("combobox"), { target: { value: `web_search/${type}` } });
  return await screen.findByTestId("data-handling");
}

describe("data_handling editor (S40-01j)", () => {
  it("is four fields, not raw JSON, and says what Choregos knows and that only stricter is allowed", async () => {
    const editor = await openDeclareForm("brave");
    expect(within(editor).getByLabelText("Query retention (days)")).toBeInTheDocument();
    expect(within(editor).getByLabelText(/Queries reused/)).toBeInTheDocument();
    expect(within(editor).getByLabelText(/Region where/)).toBeInTheDocument();
    expect(within(editor).getByLabelText(/Terms reference/)).toBeInTheDocument();
    expect(screen.getByTestId("data-handling-declared")).toHaveTextContent(
      "retention unknown, reuse unknown, region unknown",
    );
    expect(screen.getByTestId("data-handling-declared")).toHaveTextContent("can only be stricter");
  });

  it("shows a backend's declared values", async () => {
    await openDeclareForm("strict");
    expect(screen.getByTestId("data-handling-declared")).toHaveTextContent(
      "retention 30 days, reuse no, region eu",
    );
  });

  it("sends nothing when nothing is declared", async () => {
    await openDeclareForm("brave");
    fireEvent.click(screen.getByRole("button", { name: "Declare" }));
    await waitFor(() => expect(api.createOrgConnector).toHaveBeenCalledTimes(1));
    expect(api.createOrgConnector.mock.calls[0]![1].config).toEqual({});
  });

  it("sends the typed values when they come with their terms", async () => {
    const editor = await openDeclareForm("brave");
    fireEvent.change(within(editor).getByLabelText("Query retention (days)"), { target: { value: "0" } });
    fireEvent.change(within(editor).getByLabelText(/Queries reused/), { target: { value: "false" } });
    fireEvent.change(within(editor).getByLabelText(/Terms reference/), { target: { value: "contract 12, s3" } });
    fireEvent.click(screen.getByRole("button", { name: "Declare" }));
    await waitFor(() => expect(api.createOrgConnector).toHaveBeenCalledTimes(1));
    expect(api.createOrgConnector.mock.calls[0]![1].config).toEqual({
      data_handling: { query_retention_days: 0, query_reuse: false, terms_ref: "contract 12, s3" },
    });
  });

  it("refuses a declaration without its terms_ref", async () => {
    const editor = await openDeclareForm("brave");
    fireEvent.change(within(editor).getByLabelText("Query retention (days)"), { target: { value: "0" } });
    expect(within(editor).getByRole("alert")).toHaveTextContent("terms_ref");
    expect(screen.getByRole("button", { name: "Declare" })).toBeDisabled();
    fireEvent.change(within(editor).getByLabelText(/Terms reference/), { target: { value: "contract" } });
    expect(within(editor).queryByRole("alert")).toBeNull();
    expect(screen.getByRole("button", { name: "Declare" })).toBeEnabled();
  });

  it("refuses a widening of what the backend declares", async () => {
    const editor = await openDeclareForm("strict");
    fireEvent.change(within(editor).getByLabelText(/Terms reference/), { target: { value: "contract" } });
    fireEvent.change(within(editor).getByLabelText("Query retention (days)"), { target: { value: "90" } });
    expect(within(editor).getByRole("alert")).toHaveTextContent("can only be stricter");
    expect(screen.getByRole("button", { name: "Declare" })).toBeDisabled();
    fireEvent.change(within(editor).getByLabelText("Query retention (days)"), { target: { value: "7" } });
    expect(screen.getByRole("button", { name: "Declare" })).toBeEnabled();
  });

  it("edits an installed connector's declaration and blocks Save on a problem", async () => {
    api.orgConnectors.mockResolvedValue([
      installed("brave", { data_handling: { query_retention_days: 0, terms_ref: "contract" } }),
    ]);
    wrap(<OrgConnectorsPage />);
    const panel = await screen.findByTestId("secrets-search-brave");
    fireEvent.click(within(panel).getByRole("button", { name: "Edit settings" }));
    expect(within(panel).getByLabelText("Query retention (days)")).toHaveValue(0);
    fireEvent.change(within(panel).getByLabelText(/Terms reference/), { target: { value: "" } });
    expect(within(panel).getByRole("button", { name: "Save settings" })).toBeDisabled();
    fireEvent.change(within(panel).getByLabelText(/Terms reference/), { target: { value: "contract 2" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Save settings" }));
    await waitFor(() =>
      expect(api.updateOrgConnector).toHaveBeenCalledWith(
        "acme",
        "search-brave",
        expect.objectContaining({ config: { data_handling: { query_retention_days: 0, terms_ref: "contract 2" } } }),
      ),
    );
  });

  it("surfaces the API's own refusal", async () => {
    api.createOrgConnector.mockRejectedValue(new Error("data_handling: refused by the API"));
    await openDeclareForm("brave");
    fireEvent.click(screen.getByRole("button", { name: "Declare" }));
    expect(await screen.findByText("data_handling: refused by the API")).toBeInTheDocument();
  });

  it("does not turn another connector's data_handling-named field into the editor", async () => {
    api.connectorTypes.mockResolvedValue([{ ...searchType("brave"), kind: "documents" }]);
    wrap(<OrgConnectorsPage />);
    await screen.findByRole("option", { name: /Search brave/ });
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "documents/brave" } });
    await screen.findByLabelText(/data handling/);
    expect(screen.queryByTestId("data-handling")).toBeNull();
  });
});

describe("dataHandlingProblems", () => {
  it("accepts an empty or absent declaration", () => {
    expect(dataHandlingProblems("brave", undefined)).toEqual([]);
    expect(dataHandlingProblems("brave", {})).toEqual([]);
    expect(dataHandlingProblems("brave", { region: "", query_reuse: null })).toEqual([]);
  });
  it("refuses a negative or fractional retention, and an unknown field", () => {
    expect(dataHandlingProblems("brave", { query_retention_days: -1, terms_ref: "t" })).toHaveLength(1);
    expect(dataHandlingProblems("brave", { query_retention_days: 1.5, terms_ref: "t" })).toHaveLength(1);
    expect(dataHandlingProblems("brave", { colour: "red" })).toHaveLength(1);
  });
  it("refuses reuse and a different region where the backend declares otherwise", () => {
    expect(dataHandlingProblems("strict", { query_reuse: true, terms_ref: "t" })).toHaveLength(1);
    expect(dataHandlingProblems("strict", { region: "us", terms_ref: "t" })).toHaveLength(1);
    expect(dataHandlingProblems("strict", { region: "eu", query_reuse: false, terms_ref: "t" })).toEqual([]);
  });
});
