import { describe, expect, it } from "vitest";
import { choixPermis } from "@/components/operations-du-projet";

describe("ce qu'un projet peut choisir pour une opération (ADR 0034, S19-02)", () => {
  it("la politique de l'organisation, ou plus strict — jamais plus large", () => {
    expect(choixPermis("allowed")).toEqual(["allowed", "approval", "forbidden"]);
    expect(choixPermis("approval")).toEqual(["approval", "forbidden"]);
    expect(choixPermis("forbidden")).toEqual(["forbidden"]);
  });
});

describe("ce que dit une découverte (ADR 0034, S19-03)", () => {
  it("un outil nouveau ou dérivé se dit fermé ; rien de changé se dit aussi", async () => {
    const { resumeDeLaDecouverte } = await import("@/components/decouverte");
    expect(resumeDeLaDecouverte({ added: ["a", "b"], changed: ["c"], removed: ["d"], unchanged: 0 })).toBe(
      "2 new (closed until you open them), 1 changed their schema (closed again), 1 removed",
    );
    expect(resumeDeLaDecouverte({ added: [], changed: [], removed: [], unchanged: 3 })).toBe("nothing changed (3 tools)");
  });
});
