import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AccountPage from "@/app/account/page";
import AdminPage from "@/app/admin/page";
import { api } from "@/lib/api";
import { ThemeProvider } from "@/lib/theme";

/** The Roles card, on My account, lists roles by scope, not by repeating the organisation (S40-07). */
const memberships = vi.hoisted(() => ({ list: [] as unknown[] }));
vi.mock("@/lib/session", () => ({
  useSession: () => ({
    org: "varga",
    me: {
      display_name: "Vincent Devillers",
      email: "v@example.test",
      last_login_at: "2026-11-08T08:00:00Z",
      memberships: memberships.list,
    },
  }),
}));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      edition: vi.fn(),
      myTokens: vi.fn().mockResolvedValue([
        { id: "t1", name: "laptop-cli", scopes: ["*"], expires_at: null, last_used_at: null },
      ]),
      policyCeiling: vi.fn().mockResolvedValue({}),
    },
  };
});

const row = (org: string, role: string, project_slug?: string) => ({ org, role, project_slug: project_slug ?? null });

/** The Roles card; the Organisation card (S40-24), in the administration, names the slug on its own. */
const rolesCard = () => screen.getByRole("heading", { name: "Roles" }).closest("section")!;

function wrap(Page: () => React.ReactNode = AccountPage) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ThemeProvider>
        <Page />
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

describe("my account, your roles", () => {
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
    expect(within(rolesCard()).queryByText("varga")).toBeNull();
  });

  it("enterprise: the organisation named once, 'organisation : org_admin', a group per organisation", async () => {
    memberships.list = [...memberships.list, row("acme", "developer")];
    vi.mocked(api.edition).mockResolvedValue({ edition: "enterprise", features: ["multi_org"] } as never);
    wrap();
    const list = await screen.findByRole("list", { name: "Your roles in varga" });
    expect(within(list).getAllByTestId("membership")[0]!.textContent).toBe("organisation : org_admin");
    expect(within(rolesCard()).getAllByText("varga")).toHaveLength(1);
    expect(screen.getByRole("list", { name: "Your roles in acme" })).toBeTruthy();
  });

  it("holds what is the person's: profile, roles, preferences and access tokens", async () => {
    vi.mocked(api.edition).mockResolvedValue({ edition: "community", features: [] } as never);
    wrap();
    expect(screen.getByRole("heading", { level: 1, name: "My account" })).toBeTruthy();
    const titles = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(titles).toEqual(["Profile", "Roles", "Preferences", "Access tokens"]);
    const profile = screen.getByRole("heading", { name: "Profile" }).closest("section")!;
    expect(within(profile).getByText("Vincent Devillers")).toBeTruthy();
    expect(within(profile).getByText("v@example.test")).toBeTruthy();
    const preferences = screen.getByRole("heading", { name: "Preferences" }).closest("section")!;
    expect(within(preferences).getByRole("radio", { name: "Light" })).toBeTruthy();
    const tokens = screen.getByRole("heading", { name: "Access tokens" }).closest("section")!;
    expect(tokens.id).toBe("tokens");
    expect(await within(tokens).findByText("laptop-cli")).toBeTruthy();
    expect(within(tokens).getByRole("button", { name: /Create an API token/ })).toBeTruthy();
  });
});

describe("administration overview", () => {
  it("opens with the Organisation card, and holds nothing of the signed-in person (S40-24)", async () => {
    vi.mocked(api.edition).mockResolvedValue({ edition: "community", features: [] } as never);
    vi.mocked(api.myTokens).mockClear();
    wrap(AdminPage);
    const titles = (await screen.findAllByRole("heading")).map((h) => h.textContent);
    expect(titles[0]).toBe("Organisation");
    expect(await screen.findByLabelText("Display name")).toBeTruthy();
    for (const title of ["Session", "Roles", "Profile", "Preferences", "Access tokens", "My access tokens"])
      expect(screen.queryByRole("heading", { name: title })).toBeNull();
    expect(screen.queryByRole("button", { name: /Create an API token/ })).toBeNull();
    expect(api.myTokens).not.toHaveBeenCalled();
  });

  it("keeps the network on its own page: no network card on the overview (S44-04)", async () => {
    vi.mocked(api.edition).mockResolvedValue({ edition: "community", features: [] } as never);
    wrap(AdminPage);
    await screen.findByRole("heading", { name: "Organisation" });
    for (const title of ["What agents may reach", "Network access", "Outbound proxy", "Network activity"])
      expect(screen.queryByRole("heading", { name: title })).toBeNull();
  });
});
