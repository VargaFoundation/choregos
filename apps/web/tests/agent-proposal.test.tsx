import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AgentProposal, latestProposal } from "@/components/agent-proposal";
import type { Run } from "@/lib/types";

const run = (fields: Partial<Run>) =>
  ({ id: "r1", status: "succeeded", stage_role: "analyse", transition_id: "t-analyse", ...fields }) as Run;

const analysis = run({
  id: "r2",
  started_at: "2026-11-02T09:29:00Z",
  agent_slug: "fon-access-analyst",
  result: {
    schema: "choregos/StageResult/v1",
    status: "done",
    summary: "analysed",
    outputs: {
      title: "Add Abouammar as lead of recrutauto",
      outcome: "reply",
      confidence: 0.9,
      summary_markdown: "## Proposal\n\nAdd this line under `members:` in `projects/recrutauto.yaml`.",
    },
  },
} as Partial<Run>);

describe("the decision card shows what the agent proposes (2026-11-02)", () => {
  it("renders the latest agent step's title, outcome, confidence and markdown", () => {
    render(<AgentProposal slug="fon" runs={[run({ started_at: "2026-11-02T09:00:00Z" }), analysis]} />);
    expect(screen.getByText("Add Abouammar as lead of recrutauto")).toBeInTheDocument();
    expect(screen.getByText("outcome: reply")).toBeInTheDocument();
    expect(screen.getByText("confidence 0.9")).toBeInTheDocument();
    // Rendered as markdown, not as raw text.
    expect(screen.getByRole("heading", { name: "Proposal" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "t-analyse" })).toHaveAttribute("href", "/p/fon/runs/r2");
  });

  it("says in words that nothing changed in a repository", () => {
    render(<AgentProposal slug="fon" runs={[analysis]} />);
    expect(screen.getByTestId("no-repository-change")).toHaveTextContent(
      "No change in the repository: this is a proposal to apply.",
    );
  });

  it("does not say so once a pull request or a commit exists", () => {
    const { rerender } = render(<AgentProposal slug="fon" runs={[analysis]} prUrl="https://github.com/o/r/pull/1" />);
    expect(screen.queryByTestId("no-repository-change")).toBeNull();
    const committed = { ...analysis, result: { ...analysis.result!, artifacts: { branch: "fon/FON-270", commits: ["abc"] } } };
    rerender(<AgentProposal slug="fon" runs={[committed]} />);
    expect(screen.queryByTestId("no-repository-change")).toBeNull();
  });

  it("renders nothing when no agent step left anything to read", () => {
    expect(latestProposal([run({ result: null })])).toBeNull();
    const { container } = render(<AgentProposal slug="fon" runs={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
