import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LiveLog } from "@/components/live-log";
import { AgentPlanView } from "@/components/agent-plan";
import { agentPlan } from "@/lib/agent-plan";
import type { RunEventDto } from "@/lib/types";

const t = (content: string, status: string, priority = "medium") => ({ content, priority, status });
const plan = (seq: number, entries: unknown[]): RunEventDto => ({
  seq,
  type: "session/update",
  ts: `2026-10-10T14:${String(seq).padStart(2, "0")}:00Z`,
  payload: { sessionId: "s", update: { sessionUpdate: "plan", entries } },
});
const message = (seq: number, text: string): RunEventDto => ({
  seq,
  type: "session/update",
  ts: "2026-10-10T14:00:00Z",
  payload: { sessionId: "s", update: { sessionUpdate: "agent_message_chunk", content: { type: "text", text } } },
});

describe("the agent's plan, re-read from the journal (S25-01)", () => {
  it("nothing without a published plan: no task guessed from the agent's messages", () => {
    expect(agentPlan([message(1, "TODO: read the code, then fix")])).toBeNull();
    expect(agentPlan([])).toBeNull();
  });

  it("the current plan is the last revision: the agent sends the whole list, it replaces", () => {
    const relu = agentPlan([
      plan(1, [t("read", "in_progress", "high"), t("fix", "pending")]),
      message(2, "je lis"),
      plan(3, [t("read", "completed", "high"), t("fix", "in_progress")]),
    ])!;
    expect(relu.revisions).toHaveLength(2);
    expect(relu.taches.map((x) => [x.contenu, x.retiree ? "removed" : x.statut])).toEqual([
      ["read", "completed"],
      ["fix", "in_progress"],
    ]);
    expect([relu.faites, relu.total]).toEqual([1, 2]);
  });

  it("a task that disappears is struck out, never counted as done, even if ticked before", () => {
    const relu = agentPlan([
      plan(1, [t("read", "completed"), t("documenter", "completed"), t("fix", "pending")]),
      plan(2, [t("read", "completed"), t("fix", "in_progress")]),
    ])!;
    expect([relu.faites, relu.total]).toEqual([1, 2]);
    expect(relu.taches.at(-1)).toEqual({ contenu: "documenter", priorite: "medium", retiree: true, avant: "completed", revision: 2 });
  });

  it("two revisions reconcile without a silent merge: a reworded task is another task", () => {
    const relu = agentPlan([
      plan(1, [t("ajouter un test", "in_progress")]),
      plan(2, [t("add regression tests", "in_progress")]),
    ])!;
    expect(relu.taches.map((x) => [x.contenu, x.retiree])).toEqual([
      ["add regression tests", false],
      ["ajouter un test", true],
    ]);
    // Spaces do not make another task; case does: the agent is the one writing.
    const espaces = agentPlan([plan(1, [t("read  the code ", "pending")]), plan(2, [t("read the code", "completed")])])!;
    expect(espaces.taches).toHaveLength(1);
  });

  it("the order is the agent's: live and stored overlap, a number counts once", () => {
    const two = plan(2, [t("read", "completed")]);
    const relu = agentPlan([two, plan(1, [t("read", "pending")]), two])!;
    expect(relu.revisions.map((r) => r.seq)).toEqual([1, 2]);
    expect(relu.faites).toBe(1);
  });

  it("a malformed entry is ignored, an unknown status or priority takes the default", () => {
    const relu = agentPlan([plan(1, [null, { status: "completed" }, { content: "read", status: "fait", priority: "urgente" }])])!;
    expect(relu.taches).toEqual([{ contenu: "read", priorite: "medium", statut: "pending", retiree: false }]);
  });
});

describe("the plan list", () => {
  it("says the current plan's progress, and strikes out what was removed", () => {
    const relu = agentPlan([
      plan(1, [t("read", "completed", "high"), t("changelog", "pending", "low")]),
      plan(2, [t("read", "completed", "high"), t("fix", "in_progress")]),
    ])!;
    render(<AgentPlanView plan={relu} />);
    expect(screen.getByTestId("plan-progression")).toHaveTextContent("1 of 2 done");
    expect(screen.getByRole("progressbar", { name: "Tasks done" })).toHaveAttribute("aria-valuenow", "1");
    const list = screen.getByRole("list", { name: "Agent plan" });
    const rows = within(list).getAllByRole("listitem");
    expect(rows.map((l) => l.getAttribute("data-status"))).toEqual(["completed", "in_progress", "removed"]);
    expect(rows[0]).toHaveTextContent("read — done");
    expect(rows[2]).toHaveTextContent("changelog removed in revision 2");
    expect(within(rows[2]!).getByText("changelog")).toHaveClass("line-through");
  });
});

describe("a run's raw journal", () => {
  it("says a plan's state and a message's text, rather than their JSON", () => {
    render(<LiveLog events={[plan(1, [t("read", "completed"), t("fix", "pending")]), message(2, "je lis le code")]} />);
    const journal = screen.getByTestId("live-log");
    expect(journal).toHaveTextContent("Plan · 1 of 2 done");
    expect(journal).toHaveTextContent("je lis le code");
    expect(journal).not.toHaveTextContent("sessionUpdate");
  });
});

describe("opencode's plan, published through its todowrite tool (S25-05)", () => {
  const todowrite = (seq: number, todos: unknown[]): RunEventDto => ({
    seq,
    type: "session/update",
    ts: `2026-10-15T08:${String(seq).padStart(2, "0")}:00Z`,
    payload: {
      sessionId: "s",
      update: { sessionUpdate: "tool_call_update", toolCallId: `c${seq}`, title: "todowrite", kind: "other", rawInput: { todos } },
    },
  });

  it("reads each todowrite call as a plan revision, with the same rules as an ACP plan", () => {
    const plan = agentPlan([
      todowrite(1, [t("Inspect the code", "in_progress", "high"), t("Fix the total", "pending", "high")]),
      todowrite(2, [t("Inspect the code", "completed", "high"), t("Fix the total", "completed", "high")]),
    ])!;
    expect(plan.revisions).toHaveLength(2);
    expect([plan.faites, plan.total]).toEqual([2, 2]);
  });

  it("a cancelled todo is removed, never counted as done", () => {
    const plan = agentPlan([
      todowrite(1, [t("Inspect the code", "completed"), t("Update the docs", "pending")]),
      todowrite(2, [t("Inspect the code", "completed"), t("Update the docs", "cancelled")]),
    ])!;
    expect([plan.faites, plan.total]).toEqual([1, 1]);
    expect(plan.taches.at(-1)).toMatchObject({ contenu: "Update the docs", retiree: true });
  });

  it("ignores the call without input and any other tool that happens to carry a list", () => {
    const withoutEntry: RunEventDto = { ...todowrite(1, []), payload: { sessionId: "s", update: { sessionUpdate: "tool_call", toolCallId: "c1", title: "todowrite", rawInput: {} } } };
    const other: RunEventDto = {
      seq: 2,
      type: "session/update",
      ts: "2026-10-15T08:02:00Z",
      payload: { sessionId: "s", update: { sessionUpdate: "tool_call_update", toolCallId: "c2", title: "Read a.py", rawInput: { todos: [t("x", "pending")] } } },
    };
    expect(agentPlan([withoutEntry, other])).toBeNull();
  });
});

