import { describe, expect, it } from "vitest";
import { isOrgDeclarable } from "@/lib/sortes-de-connecteurs";

/** S44-04: an organisation declares its own model gateway; a project's other delivery tools stay in the project. */
describe("the kinds an organisation declares", () => {
  it("offers the model gateway, never the direct one", () => {
    expect(isOrgDeclarable("gateway", "litellm")).toBe(true);
    expect(isOrgDeclarable("gateway", "openai-compatible")).toBe(true);
    expect(isOrgDeclarable("gateway", "direct")).toBe(false);
  });

  it("keeps a project's delivery tools in the project", () => {
    for (const kind of ["tracker", "scm", "ci", "cd", "runtime", "memory"]) expect(isOrgDeclarable(kind)).toBe(false);
    expect(isOrgDeclarable("mcp")).toBe(true);
  });
});
