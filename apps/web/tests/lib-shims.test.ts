import { describe, expect, it } from "vitest";

// The former French paths of three lib modules stay as shims until S45-29 removes them.
describe("former lib paths still export the new modules", () => {
  it("run-activity", async () => {
    const old = await import("@/lib/activite-du-run");
    const neu = await import("@/lib/run-activity");
    expect(Object.keys(old).sort()).toEqual(Object.keys(neu).sort());
    expect(old.activiteDuRun).toBe(neu.activiteDuRun);
    expect(old.activiteDuRun).toBe(neu.runActivity);
  });

  it("connector-kinds", async () => {
    const old = await import("@/lib/sortes-de-connecteurs");
    const neu = await import("@/lib/connector-kinds");
    expect(Object.keys(old).sort()).toEqual(Object.keys(neu).sort());
    expect(old.libelleDeSorte).toBe(neu.kindLabel);
    expect(old.titreDeSorte).toBe(neu.kindTitle);
  });

  it("agent-plan", async () => {
    const old = await import("@/lib/plan-de-l-agent");
    const neu = await import("@/lib/agent-plan");
    expect(Object.keys(old).sort()).toEqual(Object.keys(neu).sort());
    expect(old.planDeLAgent).toBe(neu.agentPlan);
  });
});
