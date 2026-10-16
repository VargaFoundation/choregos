import { describe, expect, it } from "vitest";
import { remoteReasonText, remoteTask, shortDigest } from "@/lib/remote-run";
import type { RunEventDto } from "@/lib/types";

const event = (seq: number, type: string, payload: Record<string, unknown>): RunEventDto => ({
  seq,
  type,
  ts: `2026-10-16T10:00:0${seq}Z`,
  payload,
});

describe("what a remote agent did during a run (ADR 0062)", () => {
  it("a run with no remote event is not a remote run", () => {
    expect(remoteTask([event(1, "run.started", {}), event(2, "session/update", {})])).toBeNull();
  });

  it("reads the endpoint, the skill, the pinned card, the task and its states", () => {
    const task = remoteTask([
      event(1, "run.started", {}),
      event(2, "remote.started", { endpoint: "https://a.test/rpc", skill: "work-in-jira", card_digest: "sha256:abc", protocol_version: "0.3.0" }),
      event(3, "remote.update", { state: "submitted", task_id: "t-1" }),
      event(4, "remote.update", { state: "working", task_id: "t-1" }),
      event(5, "remote.update", { state: "working", task_id: "t-1" }),
      event(6, "remote.update", { state: "completed", task_id: "t-1" }),
      event(7, "remote.finished", { state: "completed", task_id: "t-1", duration_s: 12.5, reason: null }),
    ]);
    expect(task).toMatchObject({
      endpoint: "https://a.test/rpc",
      skill: "work-in-jira",
      cardDigest: "sha256:abc",
      protocolVersion: "0.3.0",
      taskId: "t-1",
      finalState: "completed",
      durationS: 12.5,
      reason: null,
      cardChanged: null,
    });
    expect(task?.states.map((s) => s.state)).toEqual(["submitted", "working", "completed"]);
  });

  it("a card that changed is a reason, with both digests, and no task", () => {
    const task = remoteTask([event(1, "remote.card_changed", { pinned: "sha256:old", now: "sha256:new", endpoint: "https://a.test/rpc" })]);
    expect(task?.reason).toBe("remote_card_changed");
    expect(task?.cardChanged).toEqual({ pinned: "sha256:old", now: "sha256:new" });
    expect(task?.taskId).toBeNull();
  });

  it("an event seen twice (journal and live) counts once", () => {
    const update = event(3, "remote.update", { state: "working", task_id: "t-1" });
    expect(remoteTask([update, update, event(2, "remote.started", { endpoint: "e" })])?.states).toHaveLength(1);
  });

  it("says what a failure means in words a person can act on", () => {
    expect(remoteReasonText(null)).toBeNull();
    expect(remoteReasonText("remote_card_changed")).toMatch(/publish a new version/i);
    expect(remoteReasonText("remote_auth_required")).toMatch(/reconnect/i);
    expect(remoteReasonText("remote_input_required")).toMatch(/not supported yet/i);
    expect(remoteReasonText("remote_something_new")).toBe("remote something new");
  });

  it("shortens a digest to something comparable", () => {
    expect(shortDigest(`sha256:${"ab".repeat(32)}`)).toBe("sha256:abababababab…");
    expect(shortDigest(null)).toBe("—");
  });
});
