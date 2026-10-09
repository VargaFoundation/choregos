import { describe, expect, it } from "vitest";
import { titreDuChemin } from "@/app/titre-du-document";

describe("le titre d'une page (S23-05)", () => {
  it("va du plus précis au plus large, et chaque page a le sien", () => {
    expect(titreDuChemin("/")).toBe("projects · choregos");
    expect(titreDuChemin("/inbox")).toBe("inbox · choregos");
    expect(titreDuChemin("/p/billing-api")).toBe("billing-api · choregos");
    expect(titreDuChemin("/p/billing-api/board")).toBe("board · billing-api · choregos");
    expect(titreDuChemin("/p/billing-api/items/w1")).toBe("ticket w1 · billing-api · choregos");
    expect(titreDuChemin("/p/billing-api/runs/r3")).toBe("run r3 · billing-api · choregos");
    expect(titreDuChemin("/p/billing-api/workflows/default-simple/map")).toBe(
      "map · default-simple · workflows · billing-api · choregos",
    );
    expect(titreDuChemin("/admin/x/scim")).toBe("scim · section · admin · choregos");
  });
});
