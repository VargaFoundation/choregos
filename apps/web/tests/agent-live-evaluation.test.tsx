import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { EvalSuiteEditor } from "@/components/agents/trial";
import { LiveEvaluation, ReleasePipeline } from "@/components/agents/releases";
import { api } from "@/lib/api";
import type { Agent, AgentEvaluation } from "@/lib/types";

/** The live evaluation in the console (S40-02d): the button, its cost estimate, the score. */
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme", me: { email: "lea@acme.dev", memberships: [] } }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      projects: vi.fn().mockResolvedValue({ items: [{ slug: "dev" }, { slug: "ops" }], meta: { has_more: false } }),
      agentEvaluations: vi.fn(),
      startAgentEvaluation: vi.fn(),
      agentExposure: vi.fn().mockResolvedValue({ projects: [] }),
      agentMetrics: vi.fn().mockResolvedValue({ by_version: [] }),
      agentReleaseReadiness: vi.fn().mockResolvedValue({ version: 2, channel: "candidate", kind: "promote", ready: true, checks: [] }),
    },
  };
});

const agent = (): Agent => ({
  slug: "reviewer",
  kind: "internal",
  display_name: "Reviewer",
  status: "active",
  latest_version: 2,
  stable_version: 1,
  can: { release: true, publish: true, manage: true, pin: true, fork_scopes: [] },
  versions: [
    {
      version: 2,
      checksum: "x",
      channels: [],
      spec: {
        evals: [
          { name: "a", live: true, max_cost_usd: 0.4, must_contain: ["ok"] },
          { name: "b", live: true, must_contain: [] },
          { name: "level one", must_contain: ["x"] },
        ],
      },
    },
    { version: 1, checksum: "y", channels: ["stable"], spec: {} },
  ],
});

const evaluation = (over: Partial<AgentEvaluation> = {}): AgentEvaluation => ({
  id: "e1",
  version: 2,
  project: "dev",
  status: "failed",
  score: 0.5,
  budget_usd: 0.65,
  started_at: "2026-10-16T09:00:00Z",
  cases: [
    { name: "a", run_id: "r1", status: "passed", passed: true },
    { name: "b", run_id: "r2", status: "failed", passed: false, reason: "missing “ok”" },
  ],
  ...over,
});

const wrap = (node: React.ReactNode) =>
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{node}</QueryClientProvider>);

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.agentEvaluations).mockResolvedValue([]);
});

describe("the live evaluation", () => {
  it("says what it may cost, starts in the chosen project, and shows the score and the failing case", async () => {
    vi.mocked(api.startAgentEvaluation).mockResolvedValue(evaluation({ status: "running", score: null }));
    wrap(<LiveEvaluation org="acme" agent={agent()} version={2} />);
    expect(screen.getByTestId("live-estimate")).toHaveTextContent("$0.65");
    expect(screen.getByTestId("live-evaluation")).toHaveTextContent("2 live cases");
    const project = await screen.findByLabelText("Project");
    await waitFor(() => expect(within(project).getByRole("option", { name: "ops" })).toBeInTheDocument());
    fireEvent.change(project, { target: { value: "ops" } });
    vi.mocked(api.agentEvaluations).mockResolvedValue([evaluation()]);
    fireEvent.click(screen.getByRole("button", { name: "Run live evaluation" }));
    await waitFor(() => expect(api.startAgentEvaluation).toHaveBeenCalledWith("acme", "reviewer", 2, "ops"));
    const score = await screen.findByTestId("live-score");
    expect(score).toHaveTextContent("failed");
    expect(score).toHaveTextContent("score 50 %");
    expect(score).toHaveTextContent("b: missing “ok”");
  });

  it("says 'could not run' for an evaluation that had no model, and never a score", async () => {
    vi.mocked(api.agentEvaluations).mockResolvedValue([evaluation({ status: "not_run", score: null, cases: [] })]);
    wrap(<LiveEvaluation org="acme" agent={agent()} version={2} />);
    expect(await screen.findByTestId("live-score")).toHaveTextContent("could not run: no model to call");
    expect(screen.getByTestId("live-score")).not.toHaveTextContent("passed");
  });

  it("is absent when the revision has no live case, and appears in the pipeline's Revisions card", async () => {
    const none = agent();
    none.versions![0]!.spec = { evals: [{ name: "level one", must_contain: ["x"] }] };
    wrap(<LiveEvaluation org="acme" agent={none} version={2} />);
    expect(screen.queryByTestId("live-evaluation")).toBeNull();
    wrap(<ReleasePipeline org="acme" agent={agent()} history={[]} />);
    expect(await screen.findByTestId("live-evaluation")).toBeInTheDocument();
  });

  it("a case becomes live with a cost cap and an expected result", () => {
    const onChange = vi.fn();
    render(<EvalSuiteEditor cases={[{ name: "a", must_contain: [] }]} onChange={onChange} />);
    fireEvent.click(screen.getByLabelText("Live case a"));
    expect(onChange).toHaveBeenCalledWith([{ name: "a", must_contain: [], live: true }]);
    const { container } = render(
      <EvalSuiteEditor cases={[{ name: "a", must_contain: [], live: true }]} onChange={onChange} />,
    );
    fireEvent.change(within(container).getByLabelText(/Most it may spend/), { target: { value: "0.3" } });
    expect(onChange).toHaveBeenLastCalledWith([{ name: "a", must_contain: [], live: true, max_cost_usd: 0.3 }]);
    fireEvent.change(within(container).getByLabelText("Expected result"), { target: { value: "blocked" } });
    expect(onChange).toHaveBeenLastCalledWith([{ name: "a", must_contain: [], live: true, expect_status: "blocked" }]);
  });
});
