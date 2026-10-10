import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ActiviteDuRunVue } from "@/components/activite-du-run";
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

describe("l'activité d'un run, relue du journal (S25-02)", () => {
  it("chaque appel terminé apparaît une fois, avec sa durée jusqu'à la PREMIÈRE fin", () => {
    const activite = activiteDuRun([
      maj(1, 0, { sessionUpdate: "tool_call", toolCallId: "t1", title: "Read a.py", kind: "read", status: "pending" }),
      maj(2, 1, { sessionUpdate: "tool_call_update", toolCallId: "t1", status: "in_progress" }),
      maj(3, 4, { sessionUpdate: "tool_call_update", toolCallId: "t1", status: "completed" }),
      maj(4, 9, { sessionUpdate: "tool_call_update", toolCallId: "t1", status: "completed" }),
    ]);
    expect(activite.appels).toHaveLength(1);
    expect(activite.appels[0]).toMatchObject({ titre: "Read a.py", genre: "read", statut: "completed", dureeMs: 4000 });
    expect(activite.termines).toBe(1);
  });

  it("une permission refusée se voit sur l'appel qu'elle bloque — même quand la demande le précède", () => {
    const activite = activiteDuRun([
      permission(1, 0, false, "t2"),
      maj(2, 1, { sessionUpdate: "tool_call", toolCallId: "t2", title: "Edit b.py", kind: "edit", status: "pending" }),
      maj(3, 2, { sessionUpdate: "tool_call_update", toolCallId: "t2", status: "failed" }),
      permission(4, 3, true, "t3"),
    ]);
    expect(activite.appels[0]).toMatchObject({ id: "t2", statut: "failed", refus: "outside the allowed paths" });
    expect(activite.refus).toEqual([{ cible: "src/x.py", raison: "outside the allowed paths", appel: "t2" }]);
  });

  it("un sous-agent se reconnaît à son nom d'outil, pas à son genre", () => {
    const activite = activiteDuRun([
      maj(1, 0, { sessionUpdate: "tool_call", toolCallId: "a", title: "Explore", kind: "think", name: "Task" }),
      maj(2, 0, { sessionUpdate: "tool_call", toolCallId: "b", title: "Explore", kind: "think", _meta: { claudeCode: { toolName: "Task" } } }),
      maj(3, 0, { sessionUpdate: "tool_call", toolCallId: "c", title: "Think about it", kind: "think" }),
    ]);
    expect(activite.appels.map((a) => a.sousAgent)).toEqual([true, true, false]);
    expect(activite.sousAgents).toBe(2);
  });

  it("le direct et le stocké se recouvrent : un événement compte une fois ; une mise à jour orpheline crée l'appel", () => {
    const debut = maj(1, 0, { sessionUpdate: "tool_call", toolCallId: "t1", title: "make test", kind: "execute" });
    const refus = permission(3, 6, false);
    const activite = activiteDuRun([debut, refus, debut, maj(2, 5, { sessionUpdate: "tool_call_update", toolCallId: "t9", status: "completed" }), refus]);
    expect(activite.appels.map((a) => [a.id, a.titre])).toEqual([
      ["t1", "make test"],
      ["t9", "t9"],
    ]);
    // Le même refus, reçu deux fois (stocké puis direct), ne compte qu'une fois.
    expect(activite.refus).toHaveLength(1);
  });

  it("ignore ce qui n'est pas un appel d'outil : messages, plans, événements de la plateforme", () => {
    const activite = activiteDuRun([
      maj(1, 0, { sessionUpdate: "agent_message_chunk", content: { type: "text", text: "je lis" } }),
      maj(2, 0, { sessionUpdate: "plan", entries: [] }),
      { seq: 3, type: "run.result", ts: ts(1), payload: { status: "done" } },
    ]);
    expect(activite).toEqual({ appels: [], refus: [], termines: 0, sousAgents: 0 });
  });
});

describe("la carte d'activité", () => {
  it("résume le run, montre chaque appel, et le refus sur la ligne de l'appel bloqué", () => {
    render(<ActiviteDuRunVue activite={activiteDuRun(runEvents)} />);
    expect(screen.getByTestId("activite-resume")).toHaveTextContent("5 tool calls · 5 finished · 1 refused · 1 sub-agent");
    const lignes = within(screen.getByRole("table", { name: "tool calls" })).getAllByRole("row").slice(1);
    expect(lignes).toHaveLength(5);
    const bloquee = lignes.find((l) => l.getAttribute("data-appel") === "tc-4")!;
    expect(bloquee).toHaveAttribute("data-statut", "failed");
    expect(within(bloquee).getByText("denied")).toBeInTheDocument();
    expect(within(lignes.find((l) => l.getAttribute("data-appel") === "tc-2")!).getByText("sub-agent")).toBeInTheDocument();
    // Un refus que rien ne relie à un appel reste visible, à part.
    expect(screen.queryByRole("list", { name: "refusals outside a tool call" })).not.toBeInTheDocument();
  });

  it("un refus sans appel se liste à part", () => {
    render(<ActiviteDuRunVue activite={activiteDuRun([permission(1, 0, false)])} />);
    expect(within(screen.getByRole("list", { name: "refusals outside a tool call" })).getByText("src/x.py")).toBeInTheDocument();
  });
});
