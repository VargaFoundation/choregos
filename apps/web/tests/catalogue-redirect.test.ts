import { describe, expect, it, vi } from "vitest";

const { redirect } = vi.hoisted(() => ({ redirect: vi.fn() }));
vi.mock("next/navigation", () => ({ redirect }));

import FormerMcpCatalogue from "@/app/admin/connectors/catalogue/page";

describe("the old administration path of the MCP catalogue", () => {
  it("redirects to Catalogue, MCP servers", () => {
    FormerMcpCatalogue();
    expect(redirect).toHaveBeenCalledWith("/catalogue/mcp-servers");
  });
});
