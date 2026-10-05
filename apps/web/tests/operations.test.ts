import { describe, expect, it } from "vitest";
import { choixPermis } from "@/components/operations-du-projet";

describe("ce qu'un projet peut choisir pour une opération (ADR 0034, S19-02)", () => {
  it("la politique de l'organisation, ou plus strict — jamais plus large", () => {
    expect(choixPermis("allowed")).toEqual(["allowed", "approval", "forbidden"]);
    expect(choixPermis("approval")).toEqual(["approval", "forbidden"]);
    expect(choixPermis("forbidden")).toEqual(["forbidden"]);
  });
});
