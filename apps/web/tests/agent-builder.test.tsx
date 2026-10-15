import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AgentBuilder, cleanSpec, reachedOperations, runtimeKind, servedTo, skillUsable, toolMatches } from "@/components/agents/builder";
import { resumeDeLaVersion } from "@/components/agents/registre";
import { api } from "@/lib/api";
import type { AgentSpec, KnowledgeBase, OrgConnector, Skill } from "@/lib/types";

/** The agent builder (S32-01): every field of a version is editable, with what it costs and allows. */
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme", me: null }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      createAgent: vi.fn().mockResolvedValue({ slug: "triage", display_name: "Triage", latest_version: 1 }),
      publishAgentVersion: vi.fn(),
      projects: vi.fn().mockResolvedValue({ items: [{ slug: "billing-api", name: "Billing API" }], meta: { has_more: false } }),
      platformModels: vi.fn().mockResolvedValue([
        { model_name: "platform/cheap", litellm_model: "x", input_cost_per_1k: 0.0008, output_cost_per_1k: 0.004 },
      ]),
      backends: vi.fn().mockResolvedValue([
        { name: "claude-code", enabled: true, capabilities: [] },
        { name: "codex", enabled: false, capabilities: [] },
      ]),
      agentImages: vi.fn().mockResolvedValue([
        { name: "ticket-triager", digest_pinned: true },
        { name: "scratch-bench", digest_pinned: false },
      ]),
      orgConnectors: vi.fn(),
      skills: vi.fn(),
      knowledgeBases: vi.fn(),
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

const BASES: KnowledgeBase[] = [
  { slug: "network-runbooks", name: "Network runbooks", scope: "projects", projects: ["netops"], serving_mode: "index", status: "ready" },
  { slug: "hr-policies", name: "HR policies", scope: "org", serving_mode: "files", status: "ready" },
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

  it("a knowledge base says who it is served to", () => {
    expect(servedTo(BASES[1]!)).toBe("every project of the organisation");
    expect(servedTo({ ...BASES[0]!, scope: "groups", groups: ["network"] })).toBe("the projects of network");
    expect(cleanSpec({}).memory).toBe("project");
  });
});

describe("the builder", () => {
  beforeEach(() => {
    vi.mocked(api.orgConnectors).mockResolvedValue([CONNECTOR, { name: "entra", kind: "identity", type: "entra", status: "ok" }]);
    vi.mocked(api.skills).mockResolvedValue(SKILLS);
    vi.mocked(api.knowledgeBases).mockResolvedValue(BASES);
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
    fireEvent.change(within(builder).getByLabelText("Budget per month (USD)"), { target: { value: "100" } });
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
    // A knowledge base, and whose memory it keeps.
    const base = within(builder).getByLabelText("Add a knowledge base");
    await within(base).findByRole("option", { name: "Network runbooks (index)" });
    fireEvent.change(base, { target: { value: "network-runbooks" } });
    fireEvent.click(within(builder).getByRole("button", { name: "Read it" }));
    expect(within(base).queryByRole("option", { name: "Network runbooks (index)" })).toBeNull();
    fireEvent.change(within(builder).getByLabelText("Memory scope"), { target: { value: "agent" } });
    // What it costs and allows, next to the form.
    const summary = screen.getByTestId("ce-que-ca-permet");
    expect(summary).toHaveTextContent("platform/cheap");
    expect(summary).toHaveTextContent("A run stops at US$0.50");
    expect(summary).toHaveTextContent("Alerts at 50, 80 and 100 % of US$100.00 a month; suspended at 100 %.");
    expect(within(summary).getByTestId("outils-resolus")).toHaveTextContent("track_order — called freely");
    expect(within(summary).getByTestId("outils-resolus")).toHaveTextContent("track_parcel — each call asks a person, US$0.01 a call");
    expect(within(summary).getByTestId("outils-resolus")).not.toHaveTextContent("order_laptop");
    expect(within(summary).getByTestId("connaissances-resolues")).toHaveTextContent(
      "network-runbooks — searched with knowledge_search; served to projects netops.",
    );
    expect(within(summary).getByTestId("memoire-resolue")).toHaveTextContent("Its own memory within each project");

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
        budget: { run_usd: 0.5, daily_usd: 4, monthly_usd: 100 },
        skills: [{ slug: "onboarding-procedure", version: 2 }],
        mcp_servers: [{ connector: "supplier", tools: ["track_*"] }],
        knowledge: [{ slug: "network-runbooks" }],
        memory: "agent",
        evals: [],
      },
    });
    await waitFor(() => expect(done).toHaveBeenCalledWith("Triage registered as v1"));
  });

  it("a code agent: the runtime selector swaps the backend for an image, a command and an API", async () => {
    vi.mocked(api.createAgent).mockClear();
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AgentBuilder org="acme" mode={{ kind: "create" }} onDone={vi.fn()} />
      </QueryClientProvider>,
    );
    const builder = screen.getByTestId("agent-builder");
    fireEvent.change(within(builder).getByLabelText("Slug"), { target: { value: "ticket-triager" } });
    // A coding agent by default: the backend picker is there, the image fields are not.
    expect(await within(builder).findByRole("combobox", { name: "Backend" })).toBeInTheDocument();
    expect(within(builder).queryByLabelText("Image")).toBeNull();

    fireEvent.change(within(builder).getByLabelText("Runtime"), { target: { value: "code" } });
    expect(within(builder).queryByRole("combobox", { name: "Backend" })).toBeNull();
    const image = within(builder).getByLabelText("Image");
    await within(image).findByRole("option", { name: "ticket-triager" });
    expect(within(image).getByRole("option", { name: "scratch-bench (not pinned by digest)" })).toBeInTheDocument();

    // Neither an image nor a command: refused before any call.
    fireEvent.click(within(builder).getByRole("button", { name: "Create the agent" }));
    expect(await within(builder).findByText("A code agent needs an image and a command.")).toBeInTheDocument();
    expect(api.createAgent).not.toHaveBeenCalled();

    fireEvent.change(image, { target: { value: "ticket-triager" } });
    fireEvent.change(within(builder).getByLabelText("Command"), { target: { value: "python\n-m\nticket_triager\n" } });
    fireEvent.change(within(builder).getByLabelText("Model API"), { target: { value: "anthropic" } });
    expect(screen.getByTestId("runtime-resolu")).toHaveTextContent("Your code runs in the image ticket-triager");

    fireEvent.click(within(builder).getByRole("button", { name: "Create the agent" }));
    await waitFor(() => expect(api.createAgent).toHaveBeenCalled());
    const sent = vi.mocked(api.createAgent).mock.calls[0]![1];
    expect(sent.spec?.runtime).toEqual({
      kind: "code",
      image: "ticket-triager",
      command: ["python", "-m", "ticket_triager"],
      api_format: "anthropic",
    });
    expect(sent.spec?.backend).toBeNull();
  });

  it("switching back to a coding agent drops the runtime", () => {
    const code: AgentSpec = {
      backend: null,
      runtime: { kind: "code", image: "ticket-triager", command: ["python"], api_format: "openai" },
    };
    expect(runtimeKind(code)).toBe("code");
    expect(cleanSpec(code).runtime).toMatchObject({ kind: "code", image: "ticket-triager" });
    expect(cleanSpec({ backend: "codex" })).not.toHaveProperty("runtime");
    expect(runtimeKind({ backend: "codex" })).toBe("acp");
    expect(resumeDeLaVersion(code)).toBe("code · ticket-triager");
  });
});
