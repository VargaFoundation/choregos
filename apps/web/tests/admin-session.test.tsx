import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AdminPage from "@/app/admin/page";
import { api } from "@/lib/api";

/** The Session card lists roles by scope, not by repeating the organisation (S40-07). */
const memberships = vi.hoisted(() => ({ list: [] as unknown[] }));
vi.mock("@/lib/session", () => ({
  useSession: () => ({
    org: "varga",
    me: { display_name: "Vincent", email: "v@example.test", memberships: memberships.list },
  }),
}));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      edition: vi.fn(),
      myTokens: vi.fn().mockResolvedValue([]),
      policyCeiling: vi.fn().mockResolvedValue({}),
    },
  };
});

const row = (org: string, role: string, project_slug?: string) => ({ org, role, project_slug: project_slug ?? null });

function wrap() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AdminPage />
    </QueryClientProvider>,
  );
}

describe("administration overview, your roles", () => {
  beforeEach(() => {
    memberships.list = [
      row("varga", "org_admin"),
      row("varga", "project_owner", "essai-it4it"),
      row("varga", "project_owner", "billing"),
    ];
  });

  it("community: platform : org_admin, project/<slug> : role, no organisation slug", async () => {
    vi.mocked(api.edition).mockResolvedValue({ edition: "community", features: [] } as never);
    wrap();
    const list = await screen.findByRole("list", { name: "Your roles" });
    await waitFor(() =>
      expect(within(list).getAllByTestId("membership").map((li) => li.textContent)).toEqual([
        "platform : org_admin",
        "project/billing : project_owner",
        "project/essai-it4it : project_owner",
      ]),
    );
    expect(within(list).queryByText(/varga/)).toBeNull();
    expect(screen.queryByText("varga")).toBeNull();
  });

  it("enterprise: the organisation named once, 'organisation : org_admin', a group per organisation", async () => {
    memberships.list = [...memberships.list, row("acme", "developer")];
    vi.mocked(api.edition).mockResolvedValue({ edition: "enterprise", features: ["multi_org"] } as never);
    wrap();
    const list = await screen.findByRole("list", { name: "Your roles in varga" });
    expect(within(list).getAllByTestId("membership")[0]!.textContent).toBe("organisation : org_admin");
    expect(screen.getAllByText("varga")).toHaveLength(1);
    expect(screen.getByRole("list", { name: "Your roles in acme" })).toBeTruthy();
  });
});
