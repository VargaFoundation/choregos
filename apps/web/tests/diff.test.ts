import { describe, expect, it } from "vitest";
import { diffLines, hunks } from "@/lib/diff";

describe("le diff de deux versions d'un workflow (S16-13)", () => {
  it("dit la ligne retirée et la ligne ajoutée, et rien d'autre", () => {
    const avant = "a\nb\ngates: [scope_respected]\nd";
    const apres = "a\nb\ngates: [scope_respected, ci_green]\nd";
    const changes = diffLines(avant, apres).filter((ligne) => ligne.kind !== "same");
    expect(changes).toEqual([
      { kind: "del", text: "gates: [scope_respected]", before: 3 },
      { kind: "add", text: "gates: [scope_respected, ci_green]", after: 3 },
    ]);
  });

  it("garde l'ordre des lignes : relire le diff redonne chaque version", () => {
    const avant = "un\ndeux\ntrois\nquatre\ncinq";
    const apres = "zéro\nun\ntrois\nquatre bis\ncinq\nsix";
    const lignes = diffLines(avant, apres);
    expect(lignes.filter((l) => l.kind !== "add").map((l) => l.text).join("\n")).toBe(avant);
    expect(lignes.filter((l) => l.kind !== "del").map((l) => l.text).join("\n")).toBe(apres);
  });

  it("ne montre que les morceaux qui changent, avec leur contexte", () => {
    const avant = Array.from({ length: 20 }, (_, i) => `ligne ${i}`).join("\n");
    const apres = avant.replace("ligne 3", "ligne trois").replace("ligne 15", "ligne quinze");
    const morceaux = hunks(diffLines(avant, apres));
    expect(morceaux).toHaveLength(2);
    expect(morceaux[0]?.map((l) => l.text)).toEqual(["ligne 1", "ligne 2", "ligne 3", "ligne trois", "ligne 4", "ligne 5"]);
    expect(hunks(diffLines(avant, avant))).toEqual([]);
  });
});
