import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StepDetails } from "@/components/workflows/step-details";
import type { AgentResolution, ArtifactFlow, ProcessStep } from "@/lib/types";
import devComplex from "@/mocks/dev-complex.json";

// The steps and the artifact flow the API renders for the `dev-complex` template.
const steps = devComplex.process as unknown as ProcessStep[];
const flow = devComplex.graph.artifacts as unknown as ArtifactFlow[];
const parId = new Map(steps.map((s) => [s.id, s]));
const step = (id: string) => parId.get(id)!;

const resolved = (slug: string, name: string, extra: Partial<AgentResolution> = {}): AgentResolution => ({
  ref: slug,
  slug,
  status: "resolved",
  version: 1,
  pinned: false,
  display_name: name,
  lifecycle: "active",
  in_catalogue: true,
  ...extra,
});

function rendre(id: string, resolution?: AgentResolution) {
  return render(<StepDetails step={step(id)} resolution={resolution} flow={flow} stepsById={parId} />);
}

describe("the step card (S39-13)", () => {
  it("names the registry agent, links to it, says which version a run loads, and what the step reads and produces", () => {
    rendre("t-specify", resolved("spec-writer", "Spec writer"));
    const lien = screen.getByRole("link", { name: "Spec writer" });
    expect(lien).toHaveAttribute("href", "/agents/spec-writer");
    expect(screen.getByText("v1 · latest")).toBeInTheDocument();
    expect(screen.getByText(/role refine/)).toBeInTheDocument();
    const lit = screen.getByText("Reads").nextElementSibling as HTMLElement;
    expect(lit).toHaveTextContent("human_feedback — a person's rejection reason");
    const produit = screen.getByText("Produces").nextElementSibling as HTMLElement;
    expect(within(produit).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "spec_markdown",
      "allowed_paths",
    ]);
  });

  it("says where a document comes from: the step that produces it, by its state's name", () => {
    rendre("t-implement", resolved("developer", "Developer", { pinned: true }));
    expect(screen.getByText("v1 · pinned")).toBeInTheDocument();
    const lit = screen.getByText("Reads").nextElementSibling as HTMLElement;
    expect(lit).toHaveTextContent("test_report — from Tested (t-test)");
  });

  it("says a step changes the work item's branch even when it lists no output", () => {
    rendre("t-implement", resolved("developer", "Developer"));
    expect(screen.getByText("changes on the work item's branch")).toBeInTheDocument();
    expect(screen.queryByText("changes on the work item's branch", { selector: "li" })).not.toBeNull();
  });

  it("says an agent that is not installed will be installed when the workflow is published", () => {
    rendre(
      "t-specify",
      resolved("spec-writer", "x", { status: "not_installed", version: null, display_name: null, in_catalogue: true }),
    );
    expect(screen.getByText(/installs when this workflow is published/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Spec writer" })).toBeInTheDocument();
  });

  it("flags an unknown agent and an unavailable one, in the runtime's words", () => {
    const { unmount } = rendre("t-specify", resolved("spec-writer", "x", { status: "not_installed", in_catalogue: false }));
    expect(screen.getByText("unknown agent")).toBeInTheDocument();
    unmount();
    rendre("t-specify", resolved("spec-writer", "Spec writer", { status: "unavailable", reason: "agent `spec-writer` is a draft" }));
    expect(screen.getByText(/unavailable: agent `spec-writer` is a draft/)).toBeInTheDocument();
  });

  it("shows a person's group and SLA, and no agent link", () => {
    rendre("t-approve-spec");
    expect(screen.getByText("product-owners")).toBeInTheDocument();
    expect(screen.getByText("24 h")).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("shows a job's command, and warns when nothing produces what a step reads", () => {
    const job = {
      ...step("t-test"),
      actor_type: "job",
      agent: null,
      command: ["python", "run.py"],
      image: "quant-tools",
      inputs: ["ghost"],
      outputs: [],
      output_types: {},
    } as unknown as ProcessStep;
    const manquant = { name: "ghost", produced_by: [], read_by: ["t-test"], origin: "missing" } as ArtifactFlow;
    render(<StepDetails step={job} flow={[manquant]} stepsById={parId} />);
    expect(screen.getByText("python run.py")).toBeInTheDocument();
    expect(screen.getByTestId("manque-ghost")).toHaveTextContent("nothing produces it — the step reads it empty");
  });

  it("says that a step reading no allowed paths may change any file", () => {
    const dev = { ...step("t-implement"), inputs: ["allowed_paths"] } as ProcessStep;
    const manquant = { name: "allowed_paths", produced_by: [], read_by: ["t-implement"], origin: "missing" } as ArtifactFlow;
    render(<StepDetails step={dev} flow={[manquant]} stepsById={parId} />);
    expect(screen.getByTestId("manque-allowed_paths")).toHaveTextContent("may change any file of the repository (`**`)");
  });

  it("types an output and names who reads it", () => {
    const deck = { ...step("t-test"), outputs: ["deck"], output_types: { deck: "pptx" }, writes_branch: false } as ProcessStep;
    const lu = { name: "deck", type: "pptx", produced_by: ["t-test"], read_by: ["t-review"], origin: "step" } as ArtifactFlow;
    render(<StepDetails step={deck} flow={[lu]} stepsById={parId} />);
    expect(screen.getByText("pptx")).toBeInTheDocument();
    expect(screen.getByText(/read by t-review/)).toBeInTheDocument();
  });
});
