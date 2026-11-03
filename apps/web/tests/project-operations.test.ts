import { describe, expect, it } from "vitest";
import { allowedChoices } from "@/components/project-operations";

describe("what a project can choose for an operation (ADR 0034, S19-02)", () => {
  it("the organisation's policy, or stricter, never wider", () => {
    expect(allowedChoices("allowed")).toEqual(["allowed", "approval", "forbidden"]);
    expect(allowedChoices("approval")).toEqual(["approval", "forbidden"]);
    expect(allowedChoices("forbidden")).toEqual(["forbidden"]);
  });
});

describe("what a discovery says (ADR 0034, S19-03)", () => {
  it("a new or drifted tool is said closed; nothing changed is said too", async () => {
    const { discoverySummary: resumeDeLaDecouverte } = await import("@/components/discovery");
    expect(resumeDeLaDecouverte({ added: ["a", "b"], changed: ["c"], removed: ["d"], unchanged: 0 })).toBe(
      "2 new (closed until you open them), 1 changed their schema (closed again), 1 removed",
    );
    expect(resumeDeLaDecouverte({ added: [], changed: [], removed: [], unchanged: 3 })).toBe("nothing changed (3 tools)");
  });
});
