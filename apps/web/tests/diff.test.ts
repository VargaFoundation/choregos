import { describe, expect, it } from "vitest";
import { diffLines, hunks } from "@/lib/diff";

describe("the diff of two versions of a workflow (S16-13)", () => {
  it("says the removed line and the added line, and nothing else", () => {
    const avant = "a\nb\ngates: [scope_respected]\nd";
    const after = "a\nb\ngates: [scope_respected, ci_green]\nd";
    const changes = diffLines(avant, after).filter((ligne) => ligne.kind !== "same");
    expect(changes).toEqual([
      { kind: "del", text: "gates: [scope_respected]", before: 3 },
      { kind: "add", text: "gates: [scope_respected, ci_green]", after: 3 },
    ]);
  });

  it("keeps the order of the lines: reading the diff back gives each version", () => {
    const avant = "un\ndeux\ntrois\nquatre\ncinq";
    const after = "zero\nun\ntrois\nquatre bis\ncinq\nsix";
    const lines = diffLines(avant, after);
    expect(lines.filter((l) => l.kind !== "add").map((l) => l.text).join("\n")).toBe(avant);
    expect(lines.filter((l) => l.kind !== "del").map((l) => l.text).join("\n")).toBe(after);
  });

  it("shows only the chunks that change, with their context", () => {
    const avant = Array.from({ length: 20 }, (_, i) => `ligne ${i}`).join("\n");
    const after = avant.replace("ligne 3", "ligne trois").replace("ligne 15", "ligne quinze");
    const morceaux = hunks(diffLines(avant, after));
    expect(morceaux).toHaveLength(2);
    expect(morceaux[0]?.map((l) => l.text)).toEqual(["ligne 1", "ligne 2", "ligne 3", "ligne trois", "ligne 4", "ligne 5"]);
    expect(hunks(diffLines(avant, avant))).toEqual([]);
  });
});
