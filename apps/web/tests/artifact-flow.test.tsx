import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CarteEnSerpentin } from "@/components/parcours/carte-du-parcours";
import { ArtifactFlowCard, type Highlight } from "@/components/workflows/artifact-flow";
import type { ArtifactFlow, ProcessStep } from "@/lib/types";
import devComplex from "@/mocks/dev-complex.json";

const steps = devComplex.process as unknown as ProcessStep[];
const artifacts = devComplex.graph.artifacts as unknown as ArtifactFlow[];

describe("the artifacts table under the map (S39-13)", () => {
  it("has one row per artifact of the workflow, with its producers and readers", () => {
    render(<ArtifactFlowCard artifacts={artifacts} steps={steps} onHighlight={() => {}} />);
    const table = screen.getByRole("table", { name: "Artifacts" });
    expect(within(table).getAllByRole("row")).toHaveLength(artifacts.length + 1);
    const ligne = screen.getByTestId("artefact-test_report");
    expect(ligne).toHaveTextContent("t-test");
    expect(ligne).toHaveTextContent("t-implement");
    expect(screen.getByTestId("artefact-spec_markdown")).toHaveTextContent("context of every agent step");
    expect(screen.getByTestId("artefact-spec_markdown")).toHaveTextContent("in the pull request");
    expect(screen.getByTestId("artefact-human_feedback")).toHaveTextContent("on rejection");
  });

  it("focusing a row names the producing and the reading steps", () => {
    const onHighlight = vi.fn();
    render(<ArtifactFlowCard artifacts={artifacts} steps={steps} onHighlight={onHighlight} />);
    fireEvent.focus(screen.getByTestId("artefact-test_report"));
    const lit = onHighlight.mock.calls.at(-1)![0] as Highlight;
    expect(lit.get("t-test")).toBe("produces");
    expect(lit.get("t-implement")).toBe("reads");
    fireEvent.blur(screen.getByTestId("artefact-test_report"));
    expect(onHighlight.mock.calls.at(-1)![0]).toBeNull();
    fireEvent.mouseEnter(screen.getByTestId("artefact-review_markdown"));
    expect((onHighlight.mock.calls.at(-1)![0] as Highlight).get("t-address-review")).toBe("produces");
  });

  it("shows nothing against an API that predates the artifact flow", () => {
    const { container } = render(<ArtifactFlowCard artifacts={undefined} steps={steps} onHighlight={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("lights the boxes of the map the row names", () => {
    const highlight: Highlight = new Map([["t-test", "produces"], ["t-implement", "reads"]]);
    render(
      <CarteEnSerpentin
        graph={devComplex.graph as never}
        process={steps}
        initial={devComplex.initial}
        highlight={highlight}
      />,
    );
    expect(screen.getByTestId("etape-t-implement")).toHaveAttribute("data-flow", "reads");
    expect(screen.getByTestId("etape-t-test")).toHaveAttribute("data-flow", "produces");
  });
});
