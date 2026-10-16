import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import EgressPage from "@/app/admin/egress/page";
import { api } from "@/lib/api";
import type { EgressOverview } from "@/lib/types";

vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme" }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      orgEgress: vi.fn(),
      revokeOrgEgressRule: vi.fn().mockResolvedValue({}),
      approveOrgEgressRequest: vi.fn().mockResolvedValue({}),
      checkOrgEgress: vi.fn(),
    },
  };
});

const overview = (canDecide: boolean): EgressOverview => ({
  mode: "enforce",
  ceiling: { api: ["api.github.com"], workers: [] },
  rules: [
    { id: "r1", host: "api.github.com", ports: [443], used_by: ["api"], status: "allowed", origin: "migration", decided_by: "platform" },
    { id: "r2", host: "mcp.example.test", ports: [443], used_by: ["api"], status: "allowed", origin: "org-connector:vendor", decided_by: "augustin@acme.dev" },
  ],
  requests: [{ id: "q1", host: "docs.example.test", ports: [443], used_by: ["api", "workers"], status: "requested", reason: "the docs site", origin: "knowledge-source:docs", requested_by: "lea@acme.dev" }],
  can_decide: canDecide,
});

function view() {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <EgressPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => vi.clearAllMocks());

describe("/admin/egress (S39-22)", () => {
  it("shows the mode, the ceiling per family, the rules and the requests", async () => {
    vi.mocked(api.orgEgress).mockResolvedValue(overview(true));
    view();
    expect((await screen.findByTestId("egress-deployment")).textContent).toContain("enforce");
    expect(screen.getByTestId("ceiling-api").textContent).toBe("api.github.com");
    expect(screen.getByTestId("ceiling-workers").textContent).toBe("not declared");
    expect(screen.getByTestId("egress-rules").textContent).toContain("org-connector:vendor");
    expect(screen.getByTestId("egress-pending").textContent).toContain("docs.example.test");
  });

  it("lists what the platform must still open, and shows the change on demand", async () => {
    vi.mocked(api.orgEgress).mockResolvedValue(overview(true));
    vi.mocked(api.checkOrgEgress).mockResolvedValue({
      ok: false,
      verdicts: [
        {
          host: "mcp.example.test",
          port: 443,
          used_by: "api",
          org: "allowed",
          ceiling: "outside",
          can_allow: true,
          platform_change: { needed: true, snippet: "add mcp.example.test\n", owner: "the platform team" },
        },
      ],
    });
    view();
    const list = await screen.findByTestId("egress-platform-list");
    expect(list.textContent).toContain("mcp.example.test");
    expect(list.textContent).not.toContain("api.github.com");
    fireEvent.click(screen.getByRole("button", { name: "Show the change" }));
    expect(await screen.findByTestId("egress-snippet")).toHaveTextContent("add mcp.example.test");
    expect(api.checkOrgEgress).toHaveBeenCalledWith("acme", expect.objectContaining({ targets: [{ host: "mcp.example.test", port: 443, used_by: ["api"] }] }));
  });

  it("an administrator revokes and approves; anyone else sees neither button", async () => {
    vi.mocked(api.orgEgress).mockResolvedValue(overview(true));
    view();
    const revoke = await screen.findAllByRole("button", { name: "Revoke" });
    fireEvent.click(revoke[0]!);
    await waitFor(() => expect(api.revokeOrgEgressRule).toHaveBeenCalledWith("acme", "r1"));
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(api.approveOrgEgressRequest).toHaveBeenCalledWith("acme", "q1"));
  });

  it("without the right to decide, the page only reads", async () => {
    vi.mocked(api.orgEgress).mockResolvedValue(overview(false));
    view();
    await screen.findByTestId("egress-rules");
    expect(screen.queryByRole("button", { name: "Revoke" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  });
});
