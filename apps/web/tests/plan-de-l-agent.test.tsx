import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LiveLog } from "@/components/live-log";
import { PlanDeLAgent } from "@/components/plan-de-l-agent";
import { planDeLAgent } from "@/lib/plan-de-l-agent";
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

describe("le plan de l'agent, relu du journal (S25-01)", () => {
  it("rien sans plan publié : aucune tâche devinée des messages de l'agent", () => {
    expect(planDeLAgent([message(1, "TODO: lire le code, puis corriger")])).toBeNull();
    expect(planDeLAgent([])).toBeNull();
  });

  it("le plan courant est la dernière révision : l'agent envoie la liste entière, elle remplace", () => {
    const relu = planDeLAgent([
      plan(1, [t("lire", "in_progress", "high"), t("corriger", "pending")]),
      message(2, "je lis"),
      plan(3, [t("lire", "completed", "high"), t("corriger", "in_progress")]),
    ])!;
    expect(relu.revisions).toHaveLength(2);
    expect(relu.taches.map((x) => [x.contenu, x.retiree ? "removed" : x.statut])).toEqual([
      ["lire", "completed"],
      ["corriger", "in_progress"],
    ]);
    expect([relu.faites, relu.total]).toEqual([1, 2]);
  });

  it("une tâche qui disparaît est retirée, jamais comptée comme faite — même cochée avant", () => {
    const relu = planDeLAgent([
      plan(1, [t("lire", "completed"), t("documenter", "completed"), t("corriger", "pending")]),
      plan(2, [t("lire", "completed"), t("corriger", "in_progress")]),
    ])!;
    expect([relu.faites, relu.total]).toEqual([1, 2]);
    expect(relu.taches.at(-1)).toEqual({ contenu: "documenter", priorite: "medium", retiree: true, avant: "completed", revision: 2 });
  });

  it("deux révisions se réconcilient sans fusion silencieuse : une tâche reformulée est une autre tâche", () => {
    const relu = planDeLAgent([
      plan(1, [t("ajouter un test", "in_progress")]),
      plan(2, [t("ajouter des tests de régression", "in_progress")]),
    ])!;
    expect(relu.taches.map((x) => [x.contenu, x.retiree])).toEqual([
      ["ajouter des tests de régression", false],
      ["ajouter un test", true],
    ]);
    // Les espaces ne font pas une autre tâche ; la casse, si : c'est l'agent qui écrit.
    const espaces = planDeLAgent([plan(1, [t("lire  le code ", "pending")]), plan(2, [t("lire le code", "completed")])])!;
    expect(espaces.taches).toHaveLength(1);
  });

  it("l'ordre est celui de l'agent : le direct et le stocké se recouvrent, un numéro compte une fois", () => {
    const deux = plan(2, [t("lire", "completed")]);
    const relu = planDeLAgent([deux, plan(1, [t("lire", "pending")]), deux])!;
    expect(relu.revisions.map((r) => r.seq)).toEqual([1, 2]);
    expect(relu.faites).toBe(1);
  });

  it("une entrée mal formée est ignorée, un statut ou une priorité inconnus prennent le défaut", () => {
    const relu = planDeLAgent([plan(1, [null, { status: "completed" }, { content: "lire", status: "fait", priority: "urgente" }])])!;
    expect(relu.taches).toEqual([{ contenu: "lire", priorite: "medium", statut: "pending", retiree: false }]);
  });
});

describe("la liste du plan", () => {
  it("dit la progression du plan courant, et barre ce qui a été retiré", () => {
    const relu = planDeLAgent([
      plan(1, [t("lire", "completed", "high"), t("changelog", "pending", "low")]),
      plan(2, [t("lire", "completed", "high"), t("corriger", "in_progress")]),
    ])!;
    render(<PlanDeLAgent plan={relu} />);
    expect(screen.getByTestId("plan-progression")).toHaveTextContent("1 of 2 done");
    expect(screen.getByRole("progressbar", { name: "tasks done" })).toHaveAttribute("aria-valuenow", "1");
    const liste = screen.getByRole("list", { name: "agent plan" });
    const lignes = within(liste).getAllByRole("listitem");
    expect(lignes.map((l) => l.getAttribute("data-statut"))).toEqual(["completed", "in_progress", "removed"]);
    expect(lignes[0]).toHaveTextContent("lire — done");
    expect(lignes[2]).toHaveTextContent("changelog removed in revision 2");
    expect(within(lignes[2]!).getByText("changelog")).toHaveClass("line-through");
  });
});

describe("le journal brut d'un run", () => {
  it("dit l'état d'un plan et le texte d'un message, plutôt que leur JSON", () => {
    render(<LiveLog events={[plan(1, [t("lire", "completed"), t("corriger", "pending")]), message(2, "je lis le code")]} />);
    const journal = screen.getByTestId("live-log");
    expect(journal).toHaveTextContent("plan · 1 of 2 done");
    expect(journal).toHaveTextContent("je lis le code");
    expect(journal).not.toHaveTextContent("sessionUpdate");
  });
});

