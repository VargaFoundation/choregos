// @vitest-environment node
import { describe, expect, it } from "vitest";
import { diffSpecs } from "@/lib/agent-diff";
import { grantNames, keepBounds, toolsLabel, type ToolGrant } from "@/lib/mcp-tools";
import type { AgentSpec } from "@/lib/types";

/** ADR 0128 (S60-06): a grant may bind its arguments; the console names it, keeps the bound and diffs it. */
const shop: ToolGrant = { name: "sync_application", arguments: { properties: { name: { const: "shop" } } } };

describe("tool grants with bound arguments", () => {
  it("are named, and said bound", () => {
    expect(grantNames(["get_*", shop])).toEqual(["get_*", "sync_application"]);
    expect(toolsLabel(["get_*", shop])).toBe("get_*, sync_application (arguments bound)");
  });

  it("keep their bound while their name stays listed, and lose it with the name", () => {
    expect(keepBounds(["get_*", shop], ["get_*", "sync_application", "list_*"])).toEqual(["get_*", shop, "list_*"]);
    expect(keepBounds(["get_*", shop], ["get_*"])).toEqual(["get_*"]);
  });

  it("show in a version diff when only the bound changed", () => {
    const base: AgentSpec = { instructions: "Sync.", mcp_servers: [{ connector: "argocd", tools: [shop] }] };
    const billing = { ...shop, arguments: { properties: { name: { const: "billing" } } } };
    expect(diffSpecs(base, { ...base, mcp_servers: [{ connector: "argocd", tools: [shop] }] })).toEqual([]);
    const [change] = diffSpecs(base, { ...base, mcp_servers: [{ connector: "argocd", tools: [billing] }] });
    expect(change?.detail).toContain('"billing"');
  });
});
