// @vitest-environment node
import { describe, expect, it } from "vitest";
import { NO_TOOL_LABEL, toolsLabel } from "@/lib/mcp-tools";

/** S51-07: the broker opens nothing for a server named without a pattern; the console never says "every tool". */
describe("toolsLabel", () => {
  it("says an empty or absent list opens no tool, and how to open some", () => {
    expect(toolsLabel([])).toBe("no tool — name tools, or * for all");
    expect(toolsLabel(undefined)).toBe(NO_TOOL_LABEL);
    expect(toolsLabel(null)).toBe(NO_TOOL_LABEL);
  });

  it("lists the patterns otherwise, `*` included", () => {
    expect(toolsLabel(["search_*", "get_issue"])).toBe("search_*, get_issue");
    expect(toolsLabel(["*"])).toBe("*");
  });
});
