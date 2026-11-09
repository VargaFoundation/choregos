import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ArtifactFacts } from "@/components/artifact-value";
import { factsOf, groupArtifacts, titleOf } from "@/lib/artifacts";
import type { Artifact } from "@/lib/types";

function artifact(name: string, extra: Partial<Artifact>): Artifact {
  return {
    name,
    type: "level",
    kind: "value",
    media_type: "text/plain",
    version: 1,
    digest: `sha256:${"a".repeat(64)}`,
    size: 4,
    produced_by: { run_id: "r1", stage: "triage", attempt: 1, agent: "triager", agent_version: 1 },
    storage_url: null,
    ...extra,
  };
}

const LEVELS = [
  { value: "sev3", tone: "warn" as const },
  { value: "sev1", tone: "danger" as const },
];
const severity = artifact("severity", { value: "sev1", display: { type: "level", show: "facts", title: "Severity", levels: LEVELS } });
const coverage = artifact("coverage_percent", { type: "metric", value: 87, display: { type: "metric", show: "facts", unit: "percent" } });
const diff = artifact("diff", {
  type: "diff",
  kind: "diff",
  display: { type: "diff", show: "facts", diff: { files: 1, additions: 12, deletions: 0, truncated: false, withheld: [] } },
});

function facts(...artifacts: Artifact[]) {
  return factsOf(groupArtifacts(artifacts));
}

describe("what a workflow puts in About this ticket (ADR 0129)", () => {
  it("keeps only what it declared, and what can be drawn", () => {
    expect(facts(severity, diff).map((g) => g.name).sort()).toEqual(["diff", "severity"]);
    expect(facts(artifact("verdict", { value: "approve", display: { type: "level", show: "tab" } }))).toEqual([]);
    expect(facts(artifact("tampered", { value: null, display: { type: "level", show: "facts" } }))).toEqual([]);
  });

  it("leaves a ticket's size and risk to its columns", () => {
    const size = artifact("size", { value: "S", display: { type: "level", show: "facts", levels: [] } });
    expect(facts(size)).toEqual([]);
  });

  it("names a fact as the workflow titled it, or its name in words", () => {
    const [titled] = facts(severity);
    const [named] = facts(coverage);
    expect(titleOf(titled!)).toBe("Severity");
    expect(titleOf(named!)).toBe("Coverage percent");
  });

  it("draws a level, a declared metric and a measured diff", () => {
    render(
      <dl>
        <ArtifactFacts facts={facts(severity, coverage, diff)} onOpen={() => {}} />
      </dl>,
    );
    expect(screen.getByTestId("artifact-value-severity").className).toContain("failed");
    expect(screen.getByTestId("artifact-value-severity")).toHaveTextContent("Sev1");
    expect(screen.getByTestId("artifact-value-coverage_percent")).toHaveTextContent(/87%\s*Declared/);
    expect(screen.getByTestId("artifact-value-diff")).toHaveTextContent("+12 −0 · 1 file");
    expect(screen.getByTestId("artifact-value-diff")).not.toHaveTextContent("Declared");
  });

  it("says a metric a job measured is not a declaration", () => {
    const measured = artifact("latency", {
      type: "metric",
      value: 12,
      produced_by: { run_id: "r2", stage: "bench", attempt: 1, agent: null, agent_version: null },
      display: { type: "metric", show: "facts", unit: "ms" },
    });
    render(
      <dl>
        <ArtifactFacts facts={facts(measured)} onOpen={() => {}} />
      </dl>,
    );
    expect(screen.getByTestId("artifact-value-latency").textContent).toBe("12 ms");
  });

  it("opens the diff from its fact", () => {
    const onOpen = vi.fn();
    render(
      <dl>
        <ArtifactFacts facts={facts(diff)} onOpen={onOpen} />
      </dl>,
    );
    fireEvent.click(screen.getByTestId("artifact-value-diff"));
    expect(onOpen).toHaveBeenCalledWith("diff");
  });

  it("says when a diff's patch was withheld", () => {
    const secret = artifact("diff", {
      type: "diff",
      kind: "diff",
      display: { type: "diff", show: "facts", diff: { files: 1, additions: 1, deletions: 0, truncated: false, withheld: ["AWS key"] } },
    });
    render(
      <dl>
        <ArtifactFacts facts={facts(secret)} onOpen={() => {}} />
      </dl>,
    );
    expect(screen.getByTestId("artifact-value-diff")).toHaveTextContent("patch withheld");
  });
});
