import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RunActivityView } from "@/components/run-activity";
import { activiteDuRun } from "@/lib/activite-du-run";
import type { RunEventDto } from "@/lib/types";
import { runEvents } from "@/mocks/data";

const ts = (s: number) => new Date(Date.UTC(2026, 9, 10, 14, 0, s)).toISOString();
const maj = (seq: number, s: number, update: Record<string, unknown>): RunEventDto => ({
  seq,
  type: "session/update",
  ts: ts(s),
  payload: { sessionId: "s", update },
});
const permission = (seq: number, s: number, allowed: boolean, toolCallId?: string): RunEventDto => ({
  seq,
  type: "session/request_permission",
  ts: ts(s),
  payload: {
    allowed,
    kind: "edit",
    target: "src/x.py",
    reason: allowed ? "within the allowed paths" : "outside the allowed paths",
    ...(toolCallId ? { params: { sessionId: "s", toolCall: { toolCallId } } } : {}),
  },
});

describe("a run's activity, re-read from the journal (S25-02)", () => {
  it("each finished call appears once, with its duration up to the FIRST end", () => {
    const activity = activiteDuRun([
      maj(1, 0, { sessionUpdate: "tool_call", toolCallId: "t1", title: "Read a.py", kind: "read", status: "pending" }),
      maj(2, 1, { sessionUpdate: "tool_call_update", toolCallId: "t1", status: "in_progress" }),
      maj(3, 4, { sessionUpdate: "tool_call_update", toolCallId: "t1", status: "completed" }),
      maj(4, 9, { sessionUpdate: "tool_call_update", toolCallId: "t1", status: "completed" }),
    ]);
    expect(activity.appels).toHaveLength(1);
    expect(activity.appels[0]).toMatchObject({ titre: "Read a.py", genre: "read", statut: "completed", dureeMs: 4000 });
    expect(activity.termines).toBe(1);
  });

  it("a refused permission shows on the call it blocks, even when the request precedes it", () => {
    const activity = activiteDuRun([
      permission(1, 0, false, "t2"),
      maj(2, 1, { sessionUpdate: "tool_call", toolCallId: "t2", title: "Edit b.py", kind: "edit", status: "pending" }),
      maj(3, 2, { sessionUpdate: "tool_call_update", toolCallId: "t2", status: "failed" }),
      permission(4, 3, true, "t3"),
    ]);
    expect(activity.appels[0]).toMatchObject({ id: "t2", statut: "failed", refus: "outside the allowed paths" });
    expect(activity.refus).toEqual([{ cible: "src/x.py", raison: "outside the allowed paths", appel: "t2" }]);
  });

  it("a sub-agent is recognised by its tool name, not by its kind", () => {
    const activity = activiteDuRun([
      maj(1, 0, { sessionUpdate: "tool_call", toolCallId: "a", title: "Explore", kind: "think", name: "Task" }),
      maj(2, 0, { sessionUpdate: "tool_call", toolCallId: "b", title: "Explore", kind: "think", _meta: { claudeCode: { toolName: "Task" } } }),
      maj(3, 0, { sessionUpdate: "tool_call", toolCallId: "c", title: "Think about it", kind: "think" }),
    ]);
    expect(activity.appels.map((a) => a.sousAgent)).toEqual([true, true, false]);
    expect(activity.sousAgents).toBe(2);
  });

  it("live and stored overlap: an event counts once; an orphan update creates the call", () => {
    const start = maj(1, 0, { sessionUpdate: "tool_call", toolCallId: "t1", title: "make test", kind: "execute" });
    const refusal = permission(3, 6, false);
    const activity = activiteDuRun([start, refusal, start, maj(2, 5, { sessionUpdate: "tool_call_update", toolCallId: "t9", status: "completed" }), refusal]);
    expect(activity.appels.map((a) => [a.id, a.titre])).toEqual([
      ["t1", "make test"],
      ["t9", "t9"],
    ]);
    // The same refusal, received twice (stored then live), counts only once.
    expect(activity.refus).toHaveLength(1);
  });

  it("ignores what is not a tool call: messages, plans, platform events", () => {
    const activity = activiteDuRun([
      maj(1, 0, { sessionUpdate: "agent_message_chunk", content: { type: "text", text: "je lis" } }),
      maj(2, 0, { sessionUpdate: "plan", entries: [] }),
      { seq: 3, type: "run.result", ts: ts(1), payload: { status: "done" } },
    ]);
    expect(activity).toEqual({ appels: [], refus: [], termines: 0, sousAgents: 0 });
  });
});

describe("the activity card", () => {
  it("summarises the run, shows each call, and the refusal on the blocked call's row", () => {
    render(<RunActivityView activite={activiteDuRun(runEvents)} />);
    expect(screen.getByTestId("activite-resume")).toHaveTextContent("5 tool calls · 5 finished · 1 refused · 1 sub-agent");
    const rows = within(screen.getByRole("table", { name: "Tool calls" })).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(5);
    const bloquee = rows.find((l) => l.getAttribute("data-appel") === "tc-4")!;
    expect(bloquee).toHaveAttribute("data-statut", "failed");
    expect(within(bloquee).getByText("Denied")).toBeInTheDocument();
    expect(within(rows.find((l) => l.getAttribute("data-appel") === "tc-2")!).getByText("Sub-agent")).toBeInTheDocument();
    // A refusal that nothing links to a call stays visible, apart.
    expect(screen.queryByRole("list", { name: "Refusals outside a tool call" })).not.toBeInTheDocument();
  });

  it("a refusal without a call is listed apart", () => {
    render(<RunActivityView activite={activiteDuRun([permission(1, 0, false)])} />);
    expect(within(screen.getByRole("list", { name: "Refusals outside a tool call" })).getByText("src/x.py")).toBeInTheDocument();
  });
});
