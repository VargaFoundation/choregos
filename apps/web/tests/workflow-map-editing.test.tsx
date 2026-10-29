import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ActorsPanel, offeredAgents } from "@/components/workflows/actors-panel";
import { BrouillonProvider, useBrouillon } from "@/components/workflows/brouillon";
import { CarteDuWorkflow } from "@/components/workflows/carte/carte-du-workflow";
import { rowsToSchema, schemaToRows, withFields } from "@/components/workflows/fields-schema";
import { names, TransitionFields } from "@/components/workflows/transition-fields";
import { WorkflowSettingsPanel } from "@/components/workflows/workflow-settings";
import { api } from "@/lib/api";
import type { Agent, AgentCatalogueEntry, WorkflowEditResult } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      workflowNamed: vi.fn(),
      validateWorkflow: vi.fn(),
      editWorkflow: vi.fn(),
      agents: vi.fn(),
      agentCatalogue: vi.fn(),
    },
  };
});

const YAML = "apiVersion: choregos/v1\nkind: Workflow\n";
const DOCUMENT = {
  apiVersion: "choregos/v1",
  kind: "Workflow",
  metadata: { name: "requests", version: 1, inputs: { type: "object", properties: { start: { type: "string", format: "date" } } } },
  actors: { owner: { type: "human", group: "product-owners", sla_hours: 24 }, writer: { type: "agent", role: "refine", agent: "brief-writer" } },
  states: { inbox: { display: "To do", kind: "wait" }, done: { display: "Done", terminal: true } },
  transitions: [
    { id: "t-brief", from: "inbox", to: "done", by: "writer", outputs: ["spec_markdown"] },
    { id: "t-accept", from: "inbox", to: "done", by: "owner" },
  ],
};
const graph = { nodes: [], edges: [] };
const edited = (yaml = "after"): WorkflowEditResult => ({
  valid: true,
  errors: [],
  warnings: [],
  graph,
  process: [],
  yaml,
  diff: "+",
  inverse: [],
  notices: [],
});

function YamlProbe() {
  return <p data-testid="yaml">{useBrouillon().yaml}</p>;
}

async function underDraft(content: ReactNode) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <BrouillonProvider slug="hr" name="requests">
        <YamlProbe />
        {content}
      </BrouillonProvider>
    </QueryClientProvider>,
  );
  await waitFor(() => expect(screen.getByTestId("yaml")).toHaveTextContent("apiVersion"));
}

beforeEach(() => {
  vi.mocked(api.workflowNamed).mockResolvedValue({ name: "requests", version: 1, yaml: YAML, is_active: true, json: DOCUMENT } as never);
  vi.mocked(api.validateWorkflow).mockResolvedValue({ valid: true, errors: [], warnings: [], graph, process: [], document: DOCUMENT } as never);
  vi.mocked(api.editWorkflow).mockReset().mockResolvedValue(edited());
  vi.mocked(api.agents).mockResolvedValue([
    { slug: "researcher", kind: "internal", display_name: "Researcher", scope: "org", status: "active", latest_version: 1 },
    { slug: "mine", kind: "internal", display_name: "Mine", scope: "personal", status: "active", latest_version: 1 },
    { slug: "old", kind: "internal", display_name: "Old", scope: "org", lifecycle: "retired", status: "active", latest_version: 1 },
  ] as Agent[]);
  vi.mocked(api.agentCatalogue).mockResolvedValue([
    { slug: "researcher", kind: "internal", display_name: "Researcher", role: "research", summary: "", version: 1, installed: true },
    { slug: "assistant", kind: "internal", display_name: "Assistant", role: "deliver", summary: "", version: 1, installed: false },
    { slug: "claude-code", kind: "external", display_name: "Claude Code", role: "external", summary: "", version: 1, installed: false },
  ] as AgentCatalogueEntry[]);
});

describe("what the map offers (ADR 0112)", () => {
  it("offers the organisation's shareable agents, then the catalogue's to install, with their role", () => {
    const offered = offeredAgents(
      [
        { slug: "researcher", kind: "internal", display_name: "R", scope: "org" },
        { slug: "mine", kind: "internal", display_name: "M", scope: "personal" },
        { slug: "theirs", kind: "internal", display_name: "T", scope: "project" },
        { slug: "old", kind: "internal", display_name: "O", scope: "org", lifecycle: "retired" },
        { slug: "cursor", kind: "external", display_name: "C", scope: "org" },
      ] as Agent[],
      [
        { slug: "researcher", kind: "internal", role: "research", installed: true },
        { slug: "assistant", kind: "internal", role: "deliver", installed: false, display_name: "Assistant" },
      ] as AgentCatalogueEntry[],
    );
    expect(offered).toEqual([
      { slug: "researcher", display: "R", role: "research", installed: true },
      { slug: "assistant", display: "Assistant", role: "deliver", installed: false },
    ]);
  });

  it("edits fields as rows and keeps a property it cannot describe", () => {
    const schema = {
      type: "object",
      additionalProperties: false,
      required: ["start"],
      properties: { start: { type: "string", format: "date", title: "Start" }, tags: { type: "array", items: { type: "string" } } },
    };
    const rows = schemaToRows(schema);
    expect(rows.map((r) => [r.name, r.type, r.required, !!r.kept])).toEqual([
      ["start", "date", true, false],
      ["tags", "string", false, true],
    ]);
    expect(rowsToSchema(rows, schema)).toEqual(schema);
    expect(rowsToSchema([])).toBeNull();
    expect(withFields({ type: "object", properties: { a: { type: "string" } } }, { type: "object", properties: { b: { type: "number" } } })).toEqual({
      type: "object",
      properties: { a: { type: "string" }, b: { type: "number" } },
    });
    expect(names(" a, b ,a,,")).toEqual(["a", "b"]);
  });
});

describe("editing on the map (ADR 0112)", () => {
  it("adds an agent from the catalogue with its role, as one typed operation", async () => {
    await underDraft(<ActorsPanel org="acme" />);
    const panel = screen.getByTestId("actors-panel");
    expect(within(panel).getByRole("list", { name: "Actors of the workflow" })).toHaveTextContent("writer — agent `brief-writer`, role refine");
    const form = within(panel).getByRole("form", { name: "Add an actor" });
    await within(form).findByRole("option", { name: /Assistant \(assistant\) — installed when published/ });
    expect(within(form).queryByRole("option", { name: /Mine/ })).toBeNull();
    fireEvent.change(within(form).getByLabelText("Agent"), { target: { value: "assistant" } });
    expect(within(form).getByLabelText("Role")).toHaveValue("deliver");
    expect(within(form).getByLabelText("Name in the workflow")).toHaveValue("assistant");
    fireEvent.click(within(form).getByRole("button", { name: "Add the actor" }));
    await waitFor(() =>
      expect(api.editWorkflow).toHaveBeenCalledWith(YAML, [
        { op: "add_actor", name: "assistant", spec: { type: "agent", role: "deliver", agent: "assistant" } },
      ]),
    );
  });

  it("removing an actor is an operation the engine checks", async () => {
    await underDraft(<ActorsPanel org="acme" />);
    fireEvent.click(screen.getByRole("button", { name: "Remove the actor owner" }));
    await waitFor(() => expect(api.editWorkflow).toHaveBeenCalledWith(YAML, [{ op: "remove_actor", name: "owner" }]));
  });

  it("a person's task declares its fields and is written as lines, in one gesture", async () => {
    await underDraft(<TransitionFields id="t-accept" states={["inbox", "done"]} actors={["owner", "writer"]} workflows={[]} />);
    fireEvent.click(screen.getByText("A task a person does"));
    const task = within(screen.getByText("A task a person does").closest("details")!);
    fireEvent.change(task.getByLabelText("Title"), { target: { value: "Accept the deliverable" } });
    fireEvent.click(task.getByRole("button", { name: "Add a field" }));
    fireEvent.change(task.getByLabelText("Field 1 name"), { target: { value: "verdict" } });
    fireEvent.click(task.getByRole("button", { name: "Save the task" }));
    await waitFor(() => expect(api.editWorkflow).toHaveBeenCalled());
    const [, ops] = vi.mocked(api.editWorkflow).mock.calls[0]!;
    expect(ops).toEqual([
      {
        op: "set_workflow",
        field: "inputs",
        style: "block",
        value: { type: "object", properties: { start: { type: "string", format: "date" }, verdict: { type: "string" } } },
      },
      {
        op: "set_transition",
        id: "t-accept",
        field: "task",
        style: "block",
        value: { title: "Accept the deliverable", form: { type: "object", properties: { verdict: { type: "string" } } } },
      },
    ]);
  });

  it("clearing a field the transition does not have sends nothing; outputs stay on one line", async () => {
    await underDraft(<TransitionFields id="t-brief" states={["inbox", "done"]} actors={["owner", "writer"]} workflows={[]} />);
    fireEvent.click(screen.getByText("Inputs and outputs"));
    fireEvent.change(screen.getByLabelText("Writes (names, comma-separated)"), { target: { value: "spec_markdown, size" } });
    fireEvent.click(screen.getByRole("button", { name: "Save inputs and outputs" }));
    await waitFor(() =>
      expect(api.editWorkflow).toHaveBeenCalledWith(YAML, [
        { op: "set_transition", id: "t-brief", field: "outputs", value: ["spec_markdown", "size"], style: "flow" },
      ]),
    );
  });

  it("the workflow's own fields: where a request starts", async () => {
    await underDraft(<WorkflowSettingsPanel />);
    fireEvent.change(screen.getByLabelText("A request starts in"), { target: { value: "inbox" } });
    fireEvent.click(screen.getByRole("button", { name: "Save the workflow's fields" }));
    await waitFor(() =>
      expect(api.editWorkflow).toHaveBeenCalledWith(YAML, [{ op: "set_workflow", field: "initial", value: "inbox", style: "flow" }]),
    );
  });
});

describe("drawing a transition (ADR 0112 §4)", () => {
  it("dropping a state on another hands both ends to the caller, nothing else", () => {
    const onDraw = vi.fn();
    const g = {
      nodes: [
        { id: "inbox", display: "Inbox", lane: "human" },
        { id: "review", display: "Review", lane: "human" },
        { id: "done", display: "Done", lane: "terminal", terminal: true },
      ],
      edges: [
        { id: "t-1", from: "inbox", to: "review", kind: "nominal", actor: "owner", gates: [] },
        { id: "t-2", from: "review", to: "done", kind: "nominal", actor: "owner", gates: [] },
      ],
    };
    render(<CarteDuWorkflow graph={g} initial="inbox" onDraw={onDraw} />);
    const data = new Map<string, string>();
    const dataTransfer = {
      setData: (k: string, v: string) => data.set(k, v),
      getData: (k: string) => data.get(k) ?? "",
      get types() {
        return [...data.keys()];
      },
    };
    expect(screen.getByTestId("etat-inbox")).toHaveAttribute("draggable", "true");
    expect(screen.getByTestId("etat-done")).toHaveAttribute("draggable", "false");
    fireEvent.dragStart(screen.getByTestId("etat-inbox"), { dataTransfer });
    fireEvent.dragOver(screen.getByTestId("etat-done"), { dataTransfer });
    fireEvent.drop(screen.getByTestId("etat-done"), { dataTransfer });
    expect(onDraw).toHaveBeenCalledWith("inbox", "done");
    fireEvent.drop(screen.getByTestId("etat-inbox"), { dataTransfer });
    expect(onDraw).toHaveBeenCalledTimes(1);
  });
});
