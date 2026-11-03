import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";
import WorkflowGraphPage from "@/app/p/[slug]/workflows/[name]/page";
import { BrouillonProvider } from "@/components/workflows/brouillon";
import { api } from "@/lib/api";
import config from "../next.config.mjs";

/**
 * A workflow opens on its graph (S55-06): the text view was the default and the map a second tab,
 * so a workflow read as a list of sentences. Its page is now the graph, and the three gestures that
 * change the whole workflow — a step, an actor, its settings — sit above it, each in a drawer.
 */
vi.mock("next/navigation", () => ({
  useParams: () => ({ slug: "hr", name: "requests" }),
  usePathname: () => "/p/hr/workflows/requests",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      workflowNamed: vi.fn(),
      validateWorkflow: vi.fn(),
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
      <BrouillonProvider slug="hr" name="requests">
        <WorkflowGraphPage />
      </BrouillonProvider>
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
    fireEvent.click(await screen.findByTestId("etape-t-take"));
    const after = await screen.findByTestId("step-after-step");
    expect(within(after).getByRole("form", { name: "New transition from review" })).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("etape-t-accept"));
    await waitFor(() => expect(screen.queryByTestId("step-after-step")).toBeNull());
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
