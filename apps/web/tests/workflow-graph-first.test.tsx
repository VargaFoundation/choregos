import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";
import WorkflowGraphPage from "@/app/p/[slug]/workflows/[name]/page";
import { DraftProvider } from "@/components/workflows/draft";
import { operations } from "@/components/workflows/operations";
import { api } from "@/lib/api";
import config from "../next.config.mjs";

/**
 * A workflow opens on its graph (S55-06): the text view was the default and the map a second tab,
 * so a workflow read as a list of sentences. Its page is now the graph, and the three gestures that
 * change the whole workflow — a step, an actor, its settings — sit above it, each in a drawer.
 */
const search = vi.hoisted(() => ({ query: "" }));
vi.mock("next/navigation", () => ({
  useParams: () => ({ slug: "hr", name: "requests" }),
  usePathname: () => "/p/hr/workflows/requests",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(search.query),
}));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      workflowNamed: vi.fn(),
      validateWorkflow: vi.fn(),
      editWorkflow: vi.fn(),
      project: vi.fn().mockResolvedValue({ slug: "hr", org: "acme", name: "HR" }),
      workflows: vi.fn().mockResolvedValue([]),
      workflowActivity: vi.fn().mockResolvedValue({ steps: [], states: [] }),
      agents: vi.fn().mockResolvedValue([]),
      agentCatalogue: vi.fn().mockResolvedValue([]),
    },
  };
});

const DOCUMENT = {
  apiVersion: "choregos/v1",
  kind: "Workflow",
  metadata: { name: "requests", version: 1 },
  actors: { owner: { type: "human", group: "product-owners" } },
  states: { inbox: { display: "To do", kind: "wait" }, done: { display: "Done", terminal: true } },
  transitions: [{ id: "t-accept", from: "inbox", to: "done", by: "owner" }],
};
const graph = {
  nodes: [
    { id: "inbox", display: "To do", kind: "wait", lane: "human", column: 0 },
    { id: "done", display: "Done", terminal: true, lane: "human", column: 1 },
  ],
  edges: [{ id: "t-accept", from: "inbox", to: "done", actor: "owner", actor_type: "human", nominal: true }],
  artifacts: [],
};

beforeEach(() => {
  search.query = "";
  vi.mocked(api.workflowNamed).mockResolvedValue({
    name: "requests",
    version: 1,
    yaml: "apiVersion: choregos/v1\n",
    is_active: true,
    json: DOCUMENT,
  } as never);
  vi.mocked(api.validateWorkflow).mockResolvedValue({
    valid: true,
    errors: [],
    warnings: [],
    graph,
    process: [],
    document: DOCUMENT,
  } as never);
});

function graphPage() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <DraftProvider slug="hr" name="requests">
        <WorkflowGraphPage />
      </DraftProvider>
    </QueryClientProvider>,
  );
}

describe("a workflow's page", () => {
  it("is its graph, with the gestures that change the workflow above it", async () => {
    graphPage();
    expect(await screen.findByRole("heading", { name: "Graph" })).toBeInTheDocument();
    const toolbar = screen.getByRole("toolbar", { name: "Edit the workflow" });
    expect(within(toolbar).getByRole("button", { name: /Add a step/ })).toBeInTheDocument();
    expect(within(toolbar).getByRole("button", { name: /Actors/ })).toHaveTextContent("1");
    expect(within(toolbar).getByRole("button", { name: /Workflow settings/ })).toBeInTheDocument();
  });

  it("opens the actors in a drawer, not under the graph", async () => {
    graphPage();
    const toolbar = await screen.findByRole("toolbar", { name: "Edit the workflow" });
    expect(screen.queryByTestId("actors-panel")).toBeNull();
    fireEvent.click(within(toolbar).getByRole("button", { name: /Actors/ }));
    const drawer = await screen.findByRole("dialog", { name: "Actors" });
    await waitFor(() => expect(within(drawer).getByTestId("actors-panel")).toBeInTheDocument());
  });

  it("opens the step form in a drawer", async () => {
    graphPage();
    const toolbar = await screen.findByRole("toolbar", { name: "Edit the workflow" });
    fireEvent.click(within(toolbar).getByRole("button", { name: /Add a step/ }));
    const drawer = await screen.findByRole("dialog", { name: "Add a step" });
    expect(within(drawer).getByRole("form", { name: "New step" })).toBeInTheDocument();
  });
});

describe("building on the graph (S55-09)", () => {
  const twoSteps = {
    nodes: [
      { id: "inbox", display: "To do", kind: "wait", lane: "human", column: 0 },
      { id: "review", display: "In review", kind: "wait", lane: "human", column: 1 },
      { id: "done", display: "Done", terminal: true, lane: "human", column: 2 },
    ],
    edges: [
      { id: "t-take", from: "inbox", to: "review", actor: "owner", actor_type: "human", nominal: true },
      { id: "t-accept", from: "review", to: "done", actor: "owner", actor_type: "human", nominal: true },
    ],
    artifacts: [],
  };

  it("offers, from a chosen step, the step that follows it — not after the end", async () => {
    vi.mocked(api.validateWorkflow).mockResolvedValue({
      valid: true,
      errors: [],
      warnings: [],
      graph: twoSteps,
      process: [],
      document: DOCUMENT,
    } as never);
    graphPage();
    fireEvent.click(await screen.findByTestId("step-t-take"));
    const after = await screen.findByTestId("step-after-step");
    expect(within(after).getByRole("form", { name: "New transition from review" })).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("step-t-accept"));
    await waitFor(() => expect(screen.queryByTestId("step-after-step")).toBeNull());
  });
});

describe("connecting from the graph (S55-13)", () => {
  const twoSteps = {
    nodes: [
      { id: "inbox", display: "To do", kind: "wait", lane: "human", column: 0 },
      { id: "review", display: "In review", kind: "wait", lane: "human", column: 1 },
      { id: "done", display: "Done", terminal: true, lane: "human", column: 2 },
    ],
    edges: [
      { id: "t-take", from: "inbox", to: "review", actor: "owner", actor_type: "human", nominal: true },
      { id: "t-accept", from: "review", to: "done", actor: "owner", actor_type: "human", nominal: true },
    ],
    artifacts: [],
  };
  const DOCUMENT_TWO_STEPS = {
    ...DOCUMENT,
    states: { inbox: { display: "To do", kind: "wait" }, review: { display: "In review", kind: "wait" }, done: { display: "Done", terminal: true } },
    transitions: [
      { id: "t-take", from: "inbox", to: "review", by: "owner" },
      { id: "t-accept", from: "review", to: "done", by: "owner" },
    ],
  };

  beforeEach(() => {
    vi.mocked(api.validateWorkflow).mockResolvedValue({
      valid: true,
      errors: [],
      warnings: [],
      graph: twoSteps,
      process: [],
      document: DOCUMENT_TWO_STEPS,
    } as never);
  });

  it("opens, from a chosen step, the add-transition form from the state it leaves, focused", async () => {
    graphPage();
    fireEvent.click(await screen.findByTestId("step-t-accept"));
    const panels = screen.getByTestId("map-panels");
    const connect = within(panels).getByRole("button", { name: "Connect to…" });
    expect(connect).toHaveAccessibleDescription("A new transition out of In review");
    fireEvent.click(connect);
    const form = await within(panels).findByRole("form", { name: "New transition from review" });
    const card = within(panels).getByTestId("drawn-transition");
    expect(card).toContainElement(form);
    // The keyboard user lands in the form: the target is the first thing to choose.
    await waitFor(() => expect(within(form).getAllByRole("combobox")[0]).toHaveFocus());
    expect(within(panels).queryByRole("button", { name: "Connect to…" })).toBeNull();
  });

  it("writes the existing add-transition operation into the draft", async () => {
    vi.mocked(api.editWorkflow).mockResolvedValue({
      valid: true,
      errors: [],
      warnings: [],
      graph: twoSteps,
      process: [],
      document: DOCUMENT_TWO_STEPS,
      yaml: "apiVersion: choregos/v1\n# connected\n",
      diff: "+# connected",
      inverse: [],
      notices: [],
    } as never);
    graphPage();
    fireEvent.click(await screen.findByTestId("step-t-take"));
    fireEvent.click(within(screen.getByTestId("map-panels")).getByRole("button", { name: "Connect to…" }));
    const form = await screen.findByRole("form", { name: "New transition from inbox" });
    const [target] = within(form).getAllByRole("combobox");
    fireEvent.change(target!, { target: { value: "done" } });
    fireEvent.submit(form);
    await waitFor(() =>
      expect(api.editWorkflow).toHaveBeenCalledWith(expect.any(String), [operations.addTransition("inbox", "done", "owner")]),
    );
    // Written, the form closes and the gesture is offered again.
    await waitFor(() => expect(screen.queryByTestId("drawn-transition")).toBeNull());
  });

  it("is not offered in the list view, whose state panel holds the same form", async () => {
    search.query = "view=list";
    graphPage();
    fireEvent.click(await screen.findByTestId("state-review"));
    const panels = screen.getByTestId("map-panels");
    expect(within(panels).getByRole("form", { name: "New transition from review" })).toBeInTheDocument();
    expect(within(panels).queryByRole("button", { name: "Connect to…" })).toBeNull();
  });

  it("is not offered from a state that ends the workflow", async () => {
    // A terminal state off the path that a reopening leads back from: its step leaves the end.
    const withReopening = {
      ...twoSteps,
      nodes: [...twoSteps.nodes, { id: "cancelled", display: "Cancelled", terminal: true, lane: "human", column: 3 }],
      edges: [
        ...twoSteps.edges,
        { id: "t-cancel", from: "review", to: "cancelled", actor: "owner", actor_type: "human", kind: "reject" },
        { id: "t-reopen", from: "cancelled", to: "review", actor: "owner", actor_type: "human", nominal: true },
      ],
    };
    vi.mocked(api.validateWorkflow).mockResolvedValue({
      valid: true,
      errors: [],
      warnings: [],
      graph: withReopening,
      process: [],
      document: DOCUMENT_TWO_STEPS,
    } as never);
    graphPage();
    fireEvent.click(await screen.findByTestId("step-t-reopen"));
    const panels = screen.getByTestId("map-panels");
    await within(panels).findByTestId("transition-panel");
    expect(within(panels).queryByRole("button", { name: "Connect to…" })).toBeNull();
  });
});

describe("a workflow's routes", () => {
  const DIR = join(import.meta.dirname, "../src/app/p/[slug]/workflows/[name]");

  it("names the graph first and the steps second", () => {
    const layout = readFileSync(join(DIR, "layout.tsx"), "utf8");
    expect(layout.indexOf('label: "Graph"')).toBeGreaterThan(-1);
    expect(layout.indexOf('label: "Graph"')).toBeLessThan(layout.indexOf('label: "Steps"'));
    expect(layout).toContain('{ suffix: "", label: "Graph" }');
    expect(layout).toContain('{ suffix: "/steps", label: "Steps" }');
  });

  it("sends an old /map link to the workflow's page, for good", async () => {
    expect(await config.redirects!()).toContainEqual({
      source: "/p/:slug/workflows/:name/map",
      destination: "/p/:slug/workflows/:name",
      permanent: true,
    });
  });
});
