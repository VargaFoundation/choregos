import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AgentBuilder, cleanSpec, reachedOperations, skillUsable, toolMatches } from "@/components/agents/builder";
import { api } from "@/lib/api";
import type { OrgConnector, Skill } from "@/lib/types";

/** The agent builder (S32-01): every field of a version is editable, with what it costs and allows. */
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      createAgent: vi.fn().mockResolvedValue({ slug: "triage", display_name: "Triage", latest_version: 1 }),
      publishAgentVersion: vi.fn(),
      platformModels: vi.fn().mockResolvedValue([
        { model_name: "platform/cheap", litellm_model: "x", input_cost_per_1k: 0.0008, output_cost_per_1k: 0.004 },
      ]),
      backends: vi.fn().mockResolvedValue([
        { name: "claude-code", enabled: true, capabilities: [] },
        { name: "codex", enabled: false, capabilities: [] },
      ]),
      orgConnectors: vi.fn(),
      skills: vi.fn(),
    },
  };
});

const CONNECTOR: OrgConnector = {
  name: "supplier",
  kind: "mcp",
  type: "mcp",
  status: "ok",
  operations: [
    { name: "track_order", access: "read", policy: "allowed" },
    { name: "track_parcel", access: "read", policy: "approval", price_usd: 0.01 },
    { name: "order_laptop", access: "write", policy: "forbidden" },
  ],
};
const SKILLS: Skill[] = [
  { slug: "onboarding-procedure", status: "active", latest_version: 2 },
  { slug: "unreviewed-script", status: "pending_review", latest_version: 1 },
];

describe("the builder's rules", () => {
  it("a tool pattern is a name or a prefix", () => {
    expect(toolMatches("track_*", "track_order")).toBe(true);
    expect(toolMatches("track_order", "track_orders")).toBe(false);
    expect(reachedOperations(CONNECTOR, ["track_*"]).map((o) => o.name)).toEqual(["track_order", "track_parcel"]);
    expect(skillUsable(SKILLS[1]!)).toBe(false);
    expect(cleanSpec({ model: "", backend: "", mcp_servers: [{ connector: "", tools: [] }] })).toMatchObject({
      model: null,
      backend: null,
      mcp_servers: [],
    });
  });
});

describe("the builder", () => {
  beforeEach(() => {
    vi.mocked(api.orgConnectors).mockResolvedValue([CONNECTOR, { name: "entra", kind: "identity", type: "entra", status: "ok" }]);
    vi.mocked(api.skills).mockResolvedValue(SKILLS);
  });

  it("creates an agent from the form alone, every field of its version filled", async () => {
    const done = vi.fn();
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AgentBuilder org="acme" mode={{ kind: "create" }} onDone={done} />
      </QueryClientProvider>,
    );
    const builder = screen.getByTestId("agent-builder");
    fireEvent.change(within(builder).getByLabelText("Slug"), { target: { value: "triage" } });
    fireEvent.change(within(builder).getByLabelText("Name"), { target: { value: "Triage" } });
    fireEvent.change(within(builder).getByLabelText("Instructions"), { target: { value: "Sort {{ ticket.title }}." } });
    const model = within(builder).getByLabelText("Model");
    await within(model).findByRole("option", { name: "platform/cheap" });
    fireEvent.change(model, { target: { value: "platform/cheap" } });
    const backend = await within(builder).findByRole("combobox", { name: "Backend" });
    expect(within(backend).getByRole("option", { name: "codex (disabled)" })).toBeDisabled();
    fireEvent.change(backend, { target: { value: "claude-code" } });
    fireEvent.change(within(builder).getByLabelText("Max turns"), { target: { value: "12" } });
    fireEvent.change(within(builder).getByLabelText("Max minutes"), { target: { value: "20" } });
    fireEvent.change(within(builder).getByLabelText("Budget per run (USD)"), { target: { value: "0.5" } });
    fireEvent.change(within(builder).getByLabelText("Budget per day (USD)"), { target: { value: "4" } });
    // A skill not yet approved cannot be carried.
    const skill = within(builder).getByLabelText("Add a skill");
    await within(skill).findByRole("option", { name: "onboarding-procedure" });
    expect(within(skill).getByRole("option", { name: "unreviewed-script (pending review)" })).toBeDisabled();
    fireEvent.change(skill, { target: { value: "onboarding-procedure" } });
    fireEvent.change(within(builder).getByLabelText("Version"), { target: { value: "2" } });
    fireEvent.click(within(builder).getByRole("button", { name: "Attach" }));
    // An MCP server and its tool patterns — only the organisation's `mcp` connectors are offered.
    const server = within(builder).getByLabelText("Add a server");
    await within(server).findByRole("option", { name: "supplier" });
    expect(within(server).queryByRole("option", { name: "entra" })).toBeNull();
    fireEvent.change(server, { target: { value: "supplier" } });
    fireEvent.click(within(builder).getByRole("button", { name: "Add" }));
    fireEvent.change(within(builder).getByLabelText("Tool patterns of supplier"), { target: { value: "track_*" } });
    // What it costs and allows, next to the form.
    const summary = screen.getByTestId("ce-que-ca-permet");
    expect(summary).toHaveTextContent("platform/cheap");
    expect(summary).toHaveTextContent("A run stops at US$0.50");
    expect(within(summary).getByTestId("outils-resolus")).toHaveTextContent("track_order — called freely");
    expect(within(summary).getByTestId("outils-resolus")).toHaveTextContent("track_parcel — each call asks a person, US$0.01 a call");
    expect(within(summary).getByTestId("outils-resolus")).not.toHaveTextContent("order_laptop");

    fireEvent.click(within(builder).getByRole("button", { name: "Create the agent" }));
    await waitFor(() => expect(api.createAgent).toHaveBeenCalled());
    expect(api.createAgent).toHaveBeenCalledWith("acme", {
      slug: "triage",
      kind: "internal",
      display_name: "Triage",
      spec: {
        instructions: "Sort {{ ticket.title }}.",
        model: "platform/cheap",
        backend: "claude-code",
        limits: { max_turns: 12, max_minutes: 20 },
        budget: { run_usd: 0.5, daily_usd: 4 },
        skills: [{ slug: "onboarding-procedure", version: 2 }],
        mcp_servers: [{ connector: "supplier", tools: ["track_*"] }],
      },
    });
    await waitFor(() => expect(done).toHaveBeenCalledWith("Triage registered as v1"));
  });
});
