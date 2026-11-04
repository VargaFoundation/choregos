import { describe, expect, it } from "vitest";
import { runActivity } from "@/lib/run-activity";
import { agentPlan } from "@/lib/agent-plan";
import type { RunEventDto } from "@/lib/types";
import events from "./fixtures/dev-run-opencode-events.json";

// The journal of a real run on the dev tenant (choregos-sandbox-dev#25, stage implement,
// 2026-10-18, opencode over ACP), trimmed to the tool calls and their todo lists: no file content.
const journal = events as RunEventDto[];

describe("a real opencode run from the dev tenant (S25-06)", () => {
  it("its plan comes from todowrite, revision by revision, and ends all done", () => {
    const plan = agentPlan(journal);
    expect(plan).not.toBeNull();
    expect(plan!.revisions).toHaveLength(6);
    expect(plan!.revisions[0]?.tasks[0]).toEqual({
      content: "Inspect invoice total code and tests",
      priorite: "high",
      status: "in_progress",
    });
    expect(plan!.faites).toBe(6);
    expect(plan!.total).toBe(6);
    expect(plan!.tasks.some((t) => t.retiree)).toBe(false);
  });

  it("its activity counts each tool call once, however many updates it had", () => {
    const activity = runActivity(journal);
    expect(journal.filter((e) => e.type === "session/update")).toHaveLength(123);
    expect(activity.calls).toHaveLength(29);
    expect(activity.termines).toBe(29);
    expect(activity.refusals).toEqual([]);
    expect(activity.sousAgents).toBe(0);
    expect(activity.calls.map((a) => a.title)).toContain("make test");
  });
});
