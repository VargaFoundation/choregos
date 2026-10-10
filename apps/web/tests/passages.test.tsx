import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { courte, lecture, modifieeApresCoup, Passages } from "@/components/passages";
import type { HandOff, HandOffEvent } from "@/lib/types";

const A = `sha256:${"a".repeat(64)}`;
const B = `sha256:${"b".repeat(64)}`;
const C = `sha256:${"c".repeat(64)}`;
const ev = (kind: "produced" | "read", digest: string, run_id: string, stage: string): HandOffEvent => ({
  kind,
  digest,
  run_id,
  stage,
  attempt: 1,
  at: "2026-10-10T14:00:00Z",
});

describe("les reçus de passage, lus (S25-04)", () => {
  it("une lecture a lu la révision produite juste avant elle, une autre, ou une qu'aucune étape n'a produite", () => {
    const passage: HandOff = {
      output: "spec_markdown",
      current_digest: B,
      events: [
        ev("produced", A, "r1", "refine"),
        ev("read", A, "r2", "implement"),
        ev("produced", B, "r3", "refine"),
        ev("read", A, "r4", "review"),
        ev("read", C, "r5", "review"),
      ],
    };
    expect([1, 3, 4].map((rang) => lecture(passage, rang))).toEqual(["same", "other", "unknown"]);
  });

  it("une sortie modifiée après sa dernière production se voit ; retirée aussi", () => {
    const base: HandOff = { output: "spec_markdown", current_digest: A, events: [ev("produced", A, "r1", "refine")] };
    expect(modifieeApresCoup(base)).toBe(false);
    expect(modifieeApresCoup({ ...base, current_digest: B })).toBe(true);
    expect(modifieeApresCoup({ ...base, current_digest: null })).toBe(true);
    // Rien de produit (une lecture seule) : rien à comparer.
    expect(modifieeApresCoup({ output: "x", current_digest: A, events: [ev("read", A, "r1", "plan")] })).toBe(false);
    expect(courte(A)).toBe("aaaaaaaaaaaa");
  });

  it("la carte nomme chaque sortie, dit qui l'a produite et lue, et montre une modification après coup", () => {
    render(
      <Passages
        passages={[
          { output: "spec_markdown", current_digest: C, events: [ev("produced", A, "r1", "refine"), ev("read", A, "r2", "implement")] },
          { output: "profils", current_digest: B, events: [ev("produced", B, "r1", "sourcing")] },
        ]}
      />,
    );
    const [spec, profils] = [...screen.getByRole("list", { name: "hand-offs" }).querySelectorAll<HTMLElement>(":scope > li")];
    expect(spec).toHaveTextContent("specification");
    expect(within(spec!).getByTestId("modifiee-apres-coup")).toHaveTextContent("modified after it was produced");
    expect(within(spec!).getByText("the revision produced")).toBeInTheDocument();
    expect(spec).toHaveTextContent("produced by refine · attempt 1");
    expect(profils).toHaveTextContent("profils");
    expect(within(profils!).queryByTestId("modifiee-apres-coup")).not.toBeInTheDocument();
  });
});
