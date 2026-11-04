import { describe, expect, it } from "vitest";
import { atInstant, phaseStart, parked, instants, buildModel, narrate, outputsOf } from "@/components/journey/model";
import { place } from "@/components/journey/layout";
import { journeyWaiting, journeyInProgress, journeyFinished } from "@/mocks/journey";

// A fixed clock: two reads of the same journey give the same map.
const NOW = Date.parse("2026-10-08T12:00:00Z");
const running = journeyInProgress(NOW);
const finished = journeyFinished(NOW);
const model = buildModel(running);

describe("a journey's model (S22-02)", () => {
  it("the dev-complex path: sixteen steps, each carried by whoever moves it forward", () => {
    expect(model.steps.map((e) => e.id)).toEqual([
      "t-triage",
      "t-specify",
      "t-approve-spec",
      "t-plan",
      "t-implement",
      "t-test",
      "t-document",
      "t-review",
      "t-security-review",
      "t-release-notes",
      "t-open-pr",
      "t-approve-pr",
      "t-merge",
      "t-stage",
      "t-release",
      "t-verify-prod",
    ]);
    const byId = (id: string) => model.byId.get(id)!;
    expect([byId("t-implement").kind, byId("t-implement").label, byId("t-implement").action]).toEqual([
      "agent",
      "Developer",
      "writes the change",
    ]);
    expect([byId("t-approve-spec").kind, byId("t-approve-spec").label]).toEqual(["human", "Product owners"]);
    expect([byId("t-open-pr").kind, byId("t-open-pr").action]).toEqual(["platform", "opens the pull request"]);
    expect([byId("t-merge").action, byId("t-merge").gates]).toEqual(["merges the pull request", ["ci_green"]]);
    expect([byId("t-release").kind, byId("t-release").action]).toEqual(["train", "deploys to prod"]);
    expect(byId("t-implement").maxRounds).toBe(3);
    expect(byId("t-test").sentence).toMatch(/tester/);
  });

  it("a step carries its agent, what it reads and what it produces (S39-13)", () => {
    const specify = model.byId.get("t-specify")!;
    expect(specify.agentRef).toBe("spec-writer");
    expect(specify.outputs).toEqual(["spec_markdown", "allowed_paths"]);
    expect(specify.inputs).toEqual(["human_feedback"]);
    expect(outputsOf(specify)).toBe("→ spec_markdown, allowed_paths");
    const implement = model.byId.get("t-implement")!;
    expect(implement.writesBranch).toBe(true);
    expect(outputsOf(implement)).toBe("→ branch");
    expect(outputsOf(model.byId.get("t-approve-spec")!)).toBeNull();
    expect(model.byId.get("t-approve-spec")!.agentRef).toBeNull();
  });

  it("the workflow map reserves room for the outputs line under each row (S39-13)", () => {
    const without = place(model, 1100);
    const withOutputs = place(model, 1100, true);
    expect(withOutputs.height).toBeGreaterThan(without.height);
    // One more line (two 16 px rows and a 2 px margin) under every row of boxes: rows move apart by it.
    const rows = (p: ReturnType<typeof place>) => [...new Set([...p.steps.values()].map((pt) => pt.y))].sort((x, y) => x - y);
    expect(rows(withOutputs)[1]! - rows(withOutputs)[0]!).toBe(rows(without)[1]! - rows(without)[0]! + 34);
  });

  it("the detours one comes back from, and the arcs that lead to them", () => {
    expect(model.detours.map((d) => [d.state, d.step.id, d.sources, d.back])).toEqual([
      ["addressing_review", "t-address-review", ["t-review", "t-security-review", "t-approve-pr"], "implemented"],
      ["fixing_ci", "t-fix-ci", ["t-merge"], "awaiting_pr_approval"],
    ]);
    expect(model.arcs.map((a) => `${a.kind} ${a.fromStep}: ${a.from} → ${a.to}`)).toEqual([
      "loop t-approve-spec: awaiting_spec_approval → triaged",
      "loop t-test: implemented → planned",
      "detour t-review: documented → addressing_review",
      "detour t-security-review: reviewed → addressing_review",
      "detour t-approve-pr: awaiting_pr_approval → addressing_review",
      "detour t-merge: pr_approved → fixing_ci",
      "back t-address-review: addressing_review → implemented",
      "back t-fix-ci: fixing_ci → awaiting_pr_approval",
    ]);
    expect(model.exceptions.map((e) => e.id)).toEqual(["needs_human", "abandoned"]);
  });

  it("the phases follow the tracker's columns", () => {
    expect(model.phases.map((p) => `${p.label} (${p.steps.length})`)).toEqual([
      "Triaged (1)",
      "Needs review (1)",
      "Ready (2)",
      "In progress (6)",
      "In review (2)",
      "Merged (1)",
      "Staging (1)",
      "Deployed (1)",
      "Done (1)",
    ]);
  });

  it("live: the security review runs, the rounds are counted, the send-backs too", () => {
    const live = atInstant(running, model, Infinity);
    const status = (id: string) => live.steps.get(id)!.status;
    expect(live.state).toBe("reviewed");
    expect(status("t-security-review")).toBe("running");
    expect(["t-triage", "t-specify", "t-approve-spec", "t-plan", "t-implement", "t-test", "t-document", "t-review"].map(status)).toEqual(
      Array(8).fill("done"),
    );
    expect(["t-release-notes", "t-open-pr", "t-merge", "t-release"].map(status)).toEqual(Array(4).fill("upcoming"));
    expect(live.steps.get("t-test")!.rounds).toBe(3);
    expect(live.steps.get("t-review")!.verdict).toBe("approve");
    expect(live.steps.get("t-approve-spec")!.rounds).toBe(2);
    expect(live.arcs.get("implemented->planned")).toEqual({ times: 1, latest: false });
    expect(live.arcs.get("documented->addressing_review")!.times).toBe(1);
    expect(live.arcs.get("addressing_review->implemented")!.times).toBe(1);
    expect(live.arcs.get("pr_approved->fixing_ci")!.times).toBe(0);
    expect(model.phases[live.phase!]!.label).toBe("In progress");
  });

  it("replay: at every instant, the box that moves is the one the story says", () => {
    const images = instants(running);
    expect(images).toHaveLength(34);
    expect(images).toEqual([...images].sort((a, b) => a - b));
    // Just after the specification's refusal: the person sent it back, the writer starts again.
    const refusal = Date.parse(running.moves.find((m) => m.kind === "reject")!.at);
    const afterRefusal = atInstant(running, model, refusal);
    expect(afterRefusal.state).toBe("triaged");
    expect(afterRefusal.steps.get("t-approve-spec")!.status).toBe("sent_back");
    expect(afterRefusal.steps.get("t-specify")!.status).toBe("running");
    expect(afterRefusal.arcs.get("awaiting_spec_approval->triaged")).toEqual({ times: 1, latest: true });
    expect(narrate(model, afterRefusal.latest)).toBe("Product owners sent it back to Triaged.");
    // During the request for changes: the tests will run again after the detour.
    const request = Date.parse(running.moves.find((m) => m.kind === "changes_requested")!.at);
    const duringDetour = atInstant(running, model, request);
    expect(duringDetour.steps.get("t-review")!.status).toBe("sent_back");
    expect(duringDetour.steps.get("t-address-review")!.status).toBe("running");
    expect(narrate(model, duringDetour.latest)).toBe("Reviewer asked for changes: back to Addressing the review.");
    const back = atInstant(running, model, request + 10 * 60_000);
    expect(back.state).toBe("implemented");
    expect(back.steps.get("t-test")!.status).toBe("running");
  });

  it("a person who is awaited shows, a finished work item has nothing next", () => {
    const waiting = atInstant(journeyWaiting(NOW), model, Infinity);
    expect(waiting.steps.get("t-approve-spec")!.status).toBe("waiting");
    const ended = atInstant(finished, buildModel(finished), Infinity);
    expect([...ended.steps.values()].filter((e) => e.status !== "done").length).toBe(0);
    expect(ended.arcs.get("pr_approved->fixing_ci")!.times).toBe(1);
    expect(ended.steps.get("t-approve-pr")!.rounds).toBe(2);
    expect(ended.steps.get("t-release")!.attempts[0]!.decided_by).toBe("leo@acme.dev");
  });

  it("jumping to a phase: the first instant one of its steps moved", () => {
    const inProgress = model.phases.find((p) => p.label === "In progress")!;
    const t = phaseStart(running, model, inProgress)!;
    expect(atInstant(running, model, t).state).toBe("planned");
    expect(
      phaseStart(
        running,
        model,
        model.phases.find((p) => p.label === "Deployed")!,
      ),
    ).toBeNull();
  });
});

describe("a parked work item is resumed (#313)", () => {
  const latest = (kind: string) => ({
    ...running,
    moves: [...running.moves, { at: "2026-10-08T11:59:00Z", from: "triaged", to: "needs_human", kind, transition_id: "t-implement", reason: null }],
  });

  it("escalated, or stopped by a question: \"Replay the stage\" is offered", () => {
    expect(parked(latest("escalate") as typeof running)).toBe(true);
    expect(parked(latest("default") as typeof running)).toBe(true);
  });

  it("on its way, or closed: nothing to resume", () => {
    expect(parked(latest("nominal") as typeof running)).toBe(false);
    expect(parked({ ...latest("escalate"), closed: true } as typeof running)).toBe(false);
    expect(parked(undefined)).toBe(false);
  });
});
