import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CarteEnSerpentin } from "@/components/parcours/carte-du-parcours";
import { parcoursEnCours } from "@/mocks/parcours";

// La carte du gabarit dev-complex, telle que l'API la rend (`mocks/dev-complex.json`).
const { graph, process, initial } = parcoursEnCours(Date.parse("2026-10-08T12:00:00Z"));

describe("la carte d'un workflow en serpentin (S22-05)", () => {
  it("dessine chaque étape colorée par qui la porte, sans lecteur ni statut", () => {
    render(<CarteEnSerpentin graph={graph} process={process} initial={initial} />);
    const carte = screen.getByTestId("carte-en-serpentin");
    expect(carte.querySelectorAll("[data-etape]")).toHaveLength(17); // quinze étapes et deux détours
    expect(screen.queryByRole("toolbar", { name: "replay the journey" })).not.toBeInTheDocument();
    expect(screen.getByTestId("etape-t-implement")).toHaveAccessibleName(/^Developer, agent, writes the change — 0 attempts$/);
    const forme = (id: string) => carte.querySelector(`[data-etape="${id}"] .parcours-forme`)!.getAttribute("stroke");
    expect(forme("t-implement")).toBe("var(--varga-accent-strong)");
    expect(forme("t-approve-pr")).toBe("var(--varga-ink)");
    expect(forme("t-merge")).toBe("var(--varga-ink-muted)");
    expect(forme("t-release")).toBe("var(--varga-ok)");
    expect(carte.querySelectorAll("[data-arc]").length).toBeGreaterThan(5);
    expect(screen.getByRole("list", { name: "who carries each step" })).toHaveTextContent("release train");
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
    expect(screen.getByTestId("etape-t-a")).toHaveAccessibleName(/, agent,/);
    expect(screen.getByTestId("etape-t-b")).toHaveAccessibleName(/, person,/);
    expect(screen.getByTestId("etape-t-c")).toHaveAccessibleName(/release train/);
  });
});
