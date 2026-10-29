import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CarteEnSerpentin } from "@/components/parcours/carte-du-parcours";
import { COULEUR_DU_GENRE } from "@/components/workflows/carte/disposition";
import { parcoursEnCours } from "@/mocks/parcours";

// La carte du gabarit dev-complex, telle que l'API la rend (`mocks/dev-complex.json`).
const { graph, process, initial } = parcoursEnCours(Date.parse("2026-10-08T12:00:00Z"));

describe("la carte d'un workflow en serpentin (S22-05)", () => {
  it("dessine chaque étape colorée par qui la porte, sans lecteur ni statut", () => {
    render(<CarteEnSerpentin graph={graph} process={process} initial={initial} />);
    const carte = screen.getByTestId("carte-en-serpentin");
    expect(carte.querySelectorAll("[data-etape]")).toHaveLength(18); // sixteen steps and two detours
    expect(screen.queryByRole("toolbar", { name: "Replay the journey" })).not.toBeInTheDocument();
    // The visible text names the box, in its order (axe `label-content-name-mismatch`); the rest describes it.
    expect(screen.getByTestId("etape-t-implement")).toHaveAccessibleName(/^Developer\s*writes the change\s*→ branch$/);
    expect(screen.getByTestId("etape-t-implement")).toHaveAccessibleDescription("agent — 0 attempts");
    const forme = (id: string) => carte.querySelector(`[data-etape="${id}"] .parcours-forme`)!.getAttribute("stroke");
    // Prune l'agent, bleu la personne, ardoise la plateforme, vert le train (S24-04) — les couleurs
    // de la carte en liste, pour que les deux vues d'un workflow disent la même chose.
    expect(forme("t-implement")).toBe("var(--choregos-agent-ink)");
    expect(forme("t-approve-pr")).toBe("var(--choregos-running-ink)");
    expect(forme("t-merge")).toBe("var(--choregos-neutral-ink)");
    expect(forme("t-release")).toBe("var(--choregos-succeeded-ink)");
    for (const [id, genre] of [["t-implement", "agent"], ["t-approve-pr", "human"], ["t-merge", "platform"], ["t-release", "train"]] as const) {
      expect(forme(id)).toBe(COULEUR_DU_GENRE[genre].bord);
    }
    expect(carte.querySelectorAll("[data-arc]").length).toBeGreaterThan(5);
    expect(screen.getByRole("list", { name: "Who carries each step" })).toHaveTextContent("Release train");
  });

  it("in the workflow map, each box says what it produces (S39-13)", () => {
    render(<CarteEnSerpentin graph={graph} process={process} initial={initial} />);
    expect(screen.getByTestId("sorties-t-specify")).toHaveTextContent("→ spec_markdown, allowed_paths");
    expect(screen.getByTestId("sorties-t-implement")).toHaveTextContent(/^→ branch$/);
    expect(screen.queryByTestId("sorties-t-approve-spec")).not.toBeInTheDocument();
    expect(screen.getByTestId("etape-t-specify")).toHaveAccessibleName(/→ spec_markdown, allowed_paths$/);
  });

  it("without a work item, a platform box says what it waits for, not empty boxes, and every detour says why", () => {
    render(<CarteEnSerpentin graph={graph} process={process} initial={initial} />);
    const map = screen.getByTestId("carte-en-serpentin");
    expect(map.textContent).not.toContain("☐");
    expect(map.textContent).toMatch(/waits for: [a-z ,]+/);
    const labels = [...map.querySelectorAll("[data-testid^='arc-label-']")].map((e) => e.textContent);
    expect(labels).toContain("if rejected");
  });

  it("writes how many open work items wait after a state's name", () => {
    const { container } = render(<CarteEnSerpentin graph={graph} process={process} initial={initial} />);
    const state = container.querySelector("[data-segment]")!.getAttribute("data-segment")!;
    render(<CarteEnSerpentin graph={graph} process={process} initial={initial} openItems={new Map([[state, 3]])} />);
    expect(screen.getAllByTestId("carte-en-serpentin")[1]!.textContent).toContain("3 open · ");
  });

  it("a highlight puts data-flow on the boxes it names", () => {
    const highlight = new Map<string, "produces" | "reads">([["t-test", "produces"], ["t-implement", "reads"]]);
    render(<CarteEnSerpentin graph={graph} process={process} initial={initial} highlight={highlight} />);
    expect(screen.getByTestId("etape-t-test")).toHaveAttribute("data-flow", "produces");
    expect(screen.getByTestId("etape-t-implement")).toHaveAttribute("data-flow", "reads");
    expect(screen.getByTestId("etape-t-review")).not.toHaveAttribute("data-flow");
  });

  it("cliquer une étape rend sa transition, pour le panneau d'édition", () => {
    const onChoisir = vi.fn();
    render(<CarteEnSerpentin graph={graph} process={process} initial={initial} onChoisir={onChoisir} />);
    fireEvent.click(screen.getByTestId("etape-t-review"));
    expect(onChoisir).toHaveBeenCalledWith("t-review");
  });

  it("sans `actor_type` sur l'arête, le couloir de l'état dit qui la porte", () => {
    const n = (id: string, lane: string) => ({ id, display: id, kind: "work", lane });
    const e = (id: string, from: string, to: string) => ({ id, from, to, kind: "nominal", actor: "x", gates: [] });
    const brut = {
      nodes: [n("a", "agent"), n("b", "human"), n("c", "train"), n("d", "terminal")],
      edges: [e("t-a", "a", "b"), e("t-b", "b", "c"), e("t-c", "c", "d")],
    };
    render(<CarteEnSerpentin graph={brut} process={[]} initial="a" />);
    expect(screen.getByTestId("etape-t-a")).toHaveAccessibleDescription(/^agent —/);
    expect(screen.getByTestId("etape-t-b")).toHaveAccessibleDescription(/^person —/);
    expect(screen.getByTestId("etape-t-c")).toHaveAccessibleDescription(/^release train —/);
  });
});
