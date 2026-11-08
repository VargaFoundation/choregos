import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { roundOf } from "@/components/journey/journey-map";
import { RequestCard } from "@/components/ticket/request-card";
import { runOutcome } from "@/components/ticket/run-outcome";

/**
 * S59-07, from the first diametral-dev ticket in production (FON-276): the tracker's facts were one line
 * of JSON, a step resumed by a person read "round 5 of 2", and four failed triage runs looked alike.
 */
const TRACKER = {
  key: "FON-276",
  kind: "jira",
  labels: ["agent-dev", "iris"],
  answers: { Rank: "0|i01l3n:", Level: "maintainer" },
  creator: { email: null, account_id: "712020:a0", display_name: "Vincent DEVILLERS" },
  reporter: { email: null, account_id: "712020:a0", display_name: "Vincent DEVILLERS" },
};

describe("the tracker's facts", () => {
  it("are read as facts, not JSON, without Jira's bookkeeping", () => {
    render(<RequestCard body="Document the commands." fields={{ tracker: TRACKER }} />);
    const facts = screen.getByTestId("tracker-facts");
    expect(facts.textContent).toContain("Jira FON-276");
    expect(facts.textContent).toContain("Filed by");
    expect(facts.textContent).toContain("Vincent DEVILLERS");
    expect(screen.getByText("agent-dev")).toBeTruthy();
    expect(facts.textContent).toContain("maintainer");
    expect(facts.textContent).not.toContain("Rank");
    expect(screen.getByTestId("request-card").textContent).not.toContain('{"key"');
  });
});

describe("a round past the limit", () => {
  const step = { maxRounds: 2, kind: "agent" } as Parameters<typeof roundOf>[0];
  it("says it was resumed instead of 'round 5 of 2'", () => {
    const state = { rounds: 5, status: "running", verdict: null } as unknown as Parameters<typeof roundOf>[1];
    expect(roundOf(step, state)).toBe("round 5 (2 per pass, resumed by a person)");
  });
  it("keeps 'of N' within the limit", () => {
    const state = { rounds: 2, status: "running", verdict: null } as unknown as Parameters<typeof roundOf>[1];
    expect(roundOf(step, state)).toBe("round 2 of 2");
  });
});

describe("a run's outcome", () => {
  it("says why a run did not finish", () => {
    expect(runOutcome({ status: "failed", result: { status: "failed", reason: "max_turns" } })).toEqual({
      tone: "bad",
      label: "stopped at its tool-call limit",
    });
    expect(runOutcome({ status: "succeeded", result: { status: "done", reason: null } }).label).toBe("done");
  });
});
