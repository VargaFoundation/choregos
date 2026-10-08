import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CarteDuWorkflow } from "@/components/workflows/carte/carte-du-workflow";

const n = (id: string, display: string, lane = "agent", terminal = false) => ({ id, display, lane, terminal });
const graphe = {
  nodes: [
    n("inbox", "To triage"),
    n("spec", "Spec to approve", "human"),
    n("dev", "Implementing"),
    n("done", "Done", "terminal", true),
    n("needs_human", "Needs a human", "human"),
  ],
  edges: [
    { id: "t-refine", from: "inbox", to: "spec", kind: "nominal", actor: "refiner", gates: [] },
    { id: "t-approve", from: "spec", to: "dev", kind: "nominal", actor: "owner", gates: [], timeout_hours: 72 },
    { id: "t-dev", from: "dev", to: "done", kind: "nominal", actor: "dev", gates: ["scope_respected", "no_secrets"] },
    { id: "dev->needs_human:escalate:x", from: "dev", to: "needs_human", kind: "escalate", label: "échecs épuisés" },
    { id: "t-resume", from: "needs_human", to: "dev", kind: "nominal", actor: "maintainer", gates: [] },
  ],
};
const process = [
  { id: "t-refine", from: "inbox", to: "spec", actor: "refiner", actor_type: "agent" as const, who: "the agent `refiner`", outputs: ["spec_markdown"], sentence: "" },
];
const acteurs = { owner: { type: "human", group: "product-owners", sla_hours: 24 } };

function rendre(onSelect = vi.fn()) {
  render(<CarteDuWorkflow graph={graphe} process={process} initial="inbox" acteurs={acteurs} onSelect={onSelect} />);
  return onSelect;
}

describe("la carte à plat, rendue (S21-07)", () => {
  it("chaque case dit qui agit en toutes lettres, ce qu'elle produit, ses garanties, son délai", () => {
    rendre();
    const chemin = screen.getByRole("list", { name: "nominal path" });
    expect(chemin.querySelectorAll(":scope > li")).toHaveLength(4);
    expect(screen.getByTestId("etat-inbox")).toHaveTextContent("agent · refiner");
    expect(screen.getByTestId("etat-spec")).toHaveTextContent("human · owner");
    expect(screen.getByTestId("etat-spec")).toHaveTextContent("product-owners, within 24 h");
    expect(screen.getByTestId("etat-inbox").parentElement).toHaveTextContent("produces spec_markdown");
    expect(screen.getByTestId("etat-dev").parentElement).toHaveTextContent("only if scope_respected, no_secrets");
    expect(screen.getByTestId("etat-spec").parentElement).toHaveTextContent("times out after 72 h");
    // Ce qui sort du chemin est à côté, avec d'où l'on y tombe.
    const cote = screen.getByRole("list", { name: "off the main path" });
    expect(cote).toHaveTextContent("Needs a human");
    expect(cote).toHaveTextContent("reached from Implementing (when retries run out)");
  });

  it("cliquer un état ou une flèche ouvre son panneau", () => {
    const onSelect = rendre();
    fireEvent.click(screen.getByTestId("etat-spec"));
    expect(onSelect).toHaveBeenLastCalledWith("node", "spec");
    fireEvent.click(screen.getByTestId("transition-t-dev"));
    expect(onSelect).toHaveBeenLastCalledWith("edge", "t-dev");
    expect(screen.getByTestId("transition-t-dev")).toHaveTextContent("by dev · 2 gates");
  });

  it("les flèches du clavier déplacent le focus, et l'état sous le curseur est annoncé", () => {
    rendre();
    act(() => screen.getByTestId("etat-inbox").focus());
    expect(screen.getByTestId("workflow-graph-focus")).toHaveTextContent("To triage (agent): → Spec to approve (by refiner).");
    fireEvent.keyDown(screen.getByTestId("etat-inbox"), { key: "ArrowRight" });
    expect(screen.getByTestId("etat-spec")).toHaveFocus();
    fireEvent.keyDown(screen.getByTestId("etat-spec"), { key: "End" });
    expect(screen.getByTestId("etat-needs_human")).toHaveFocus();
    fireEvent.keyDown(screen.getByTestId("etat-needs_human"), { key: "Home" });
    expect(screen.getByTestId("etat-inbox")).toHaveFocus();
  });

  it("reprises et escalades se montrent au survol ou au focus de leur état, toutes sur demande", () => {
    rendre();
    expect(screen.queryByTestId("secondaires-dev")).toBeNull();
    fireEvent.mouseEnter(screen.getByTestId("etat-dev").parentElement!);
    expect(screen.getByTestId("secondaires-dev")).toHaveTextContent("when retries run out → Needs a human");
    fireEvent.mouseLeave(screen.getByTestId("etat-dev").parentElement!);
    expect(screen.queryByTestId("secondaires-dev")).toBeNull();
    fireEvent.click(screen.getByLabelText(/show every retry, rejection and escalation \(1\)/));
    expect(screen.getByTestId("secondaires-dev")).toBeInTheDocument();
  });

  it("la cible d'une escalade s'éclaire quand on survole sa source", () => {
    rendre();
    const cible = screen.getByTestId("etat-needs_human").parentElement!;
    expect(cible.style.outline).toBe("");
    fireEvent.mouseEnter(screen.getByTestId("etat-dev").parentElement!);
    expect(cible.style.outline).toContain("var(--varga-focus)");
  });
});
