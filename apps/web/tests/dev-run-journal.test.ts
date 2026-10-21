import { describe, expect, it } from "vitest";
import { activiteDuRun } from "@/lib/activite-du-run";
import { planDeLAgent } from "@/lib/plan-de-l-agent";
import type { RunEventDto } from "@/lib/types";
import events from "./fixtures/dev-run-opencode-events.json";

// The journal of a real run on the dev tenant (choregos-sandbox-dev#25, stage implement,
// 2026-10-18, opencode over ACP), trimmed to the tool calls and their todo lists: no file content.
const journal = events as RunEventDto[];

describe("a real opencode run from the dev tenant (S25-06)", () => {
  it("its plan comes from todowrite, revision by revision, and ends all done", () => {
    const plan = planDeLAgent(journal);
    expect(plan).not.toBeNull();
    expect(plan!.revisions).toHaveLength(6);
    expect(plan!.revisions[0]?.taches[0]).toEqual({
      contenu: "Inspect invoice total code and tests",
      priorite: "high",
      statut: "in_progress",
    });
    expect(plan!.faites).toBe(6);
    expect(plan!.total).toBe(6);
    expect(plan!.taches.some((t) => t.retiree)).toBe(false);
  });

  it("its activity counts each tool call once, however many updates it had", () => {
    const activite = activiteDuRun(journal);
    expect(journal.filter((e) => e.type === "session/update")).toHaveLength(123);
    expect(activite.appels).toHaveLength(29);
    expect(activite.termines).toBe(29);
    expect(activite.refus).toEqual([]);
    expect(activite.sousAgents).toBe(0);
    expect(activite.appels.map((a) => a.titre)).toContain("make test");
  });
});
