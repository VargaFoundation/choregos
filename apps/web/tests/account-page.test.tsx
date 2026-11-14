import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import { fireEvent } from "@testing-library/react";
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
        { id: "t2", name: "old-ci", scopes: ["*"], expires_at: "2026-01-01T00:00:00Z", last_used_at: null },
      ]),
      mySessions: vi.fn(),
      myLinkedAccounts: vi.fn().mockResolvedValue({ items: [], instances: [] }),
      endSession: vi.fn().mockResolvedValue(undefined),
      endOtherSessions: vi.fn().mockResolvedValue({ ended: 1 }),
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

  it("holds what is the person's: profile, sessions, access tokens, linked accounts, roles and preferences", async () => {
    vi.mocked(api.edition).mockResolvedValue({ edition: "community", features: [] } as never);
    vi.mocked(api.mySessions).mockResolvedValue([]);
    wrap();
    expect(screen.getByRole("heading", { level: 1, name: "My account" })).toBeTruthy();
    const titles = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(titles).toEqual(["Profile", "Sessions", "Access tokens", "Linked accounts", "Roles", "Preferences"]);
    // The side index leads to each of them.
    const index = screen.getByRole("navigation", { name: "Account sections" });
    expect(within(index).getAllByRole("link").map((a) => a.getAttribute("href"))).toEqual([
      "#profile",
      "#sessions",
      "#tokens",
      "#linked-accounts",
      "#roles",
      "#preferences",
    ]);
    const profile = screen.getByRole("heading", { name: "Profile" }).closest("section")!;
    expect(within(profile).getByText("Vincent Devillers")).toBeTruthy();
    expect(within(profile).getByText("v@example.test")).toBeTruthy();
    // The photo is uploaded here (ADR 0162): initials until there is one.
    expect(within(profile).getByTestId("avatar-initials").textContent).toBe("VD");
    expect(within(profile).getByRole("button", { name: "Upload a photo" })).toBeTruthy();
    const preferences = screen.getByRole("heading", { name: "Preferences" }).closest("section")!;
    expect(within(preferences).getByRole("radio", { name: "Light" })).toBeTruthy();
    const tokens = screen.getByRole("heading", { name: "Access tokens" }).closest("section")!;
    expect(tokens.id).toBe("tokens");
    expect(await within(tokens).findByText("laptop-cli")).toBeTruthy();
    expect(within(tokens).getByRole("button", { name: /Create an API token/ })).toBeTruthy();
    // An expired token says so.
    const old = within(tokens).getByText("old-ci").closest("li")!;
    expect(within(old).getByText("Expired")).toBeTruthy();
  });
});

const SESSIONS = [
  {
    id: "s-here",
    channel: "oidc",
    created_at: "2026-11-09T08:00:00Z",
    expires_at: "2099-11-09T16:00:00Z",
    last_seen_at: "2026-11-09T09:00:00Z",
    user_agent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0 Safari/537.36",
    ip: "203.0.113.24",
    current: true,
  },
  {
    id: "s-phone",
    channel: "oidc",
    created_at: "2026-11-09T07:00:00Z",
    expires_at: "2099-11-09T15:00:00Z",
    last_seen_at: "2026-11-09T07:30:00Z",
    user_agent: "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) Version/18.1 Mobile/15E148 Safari/604.1",
    ip: "198.51.100.7",
    current: false,
  },
];

describe("my account, your sessions (ADR 0130)", () => {
  beforeEach(() => {
    memberships.list = [row("varga", "org_admin")];
    vi.mocked(api.edition).mockResolvedValue({ edition: "community", features: [] } as never);
  });

  const sessionsCard = () => screen.getByRole("heading", { name: "Sessions" }).closest("section")!;

  it("lists each browser, this one marked, with its address", async () => {
    vi.mocked(api.mySessions).mockResolvedValue(SESSIONS);
    wrap();
    const list = await screen.findByRole("list", { name: "Your sessions" });
    const rows = await within(list).findAllByTestId("session");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]!).getByText("Chrome on Windows")).toBeTruthy();
    expect(within(rows[0]!).getByText("This session")).toBeTruthy();
    expect(within(rows[0]!).queryByRole("button", { name: "End" })).toBeNull();
    expect(within(rows[1]!).getByText("Safari on iOS")).toBeTruthy();
    expect(rows[1]!.textContent).toContain("198.51.100.7");
  });

  it("ends another session after a confirmation", async () => {
    vi.mocked(api.mySessions).mockResolvedValue(SESSIONS);
    wrap();
    const rows = await within(await screen.findByRole("list", { name: "Your sessions" })).findAllByTestId("session");
    fireEvent.click(within(rows[1]!).getByRole("button", { name: "End" }));
    expect(api.endSession).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: "End the session" }));
    await waitFor(() => expect(api.endSession).toHaveBeenCalledWith("s-phone"));
  });

  it("signs out of every other session, and offers it only when there is one", async () => {
    vi.mocked(api.mySessions).mockResolvedValue(SESSIONS);
    wrap();
    fireEvent.click(await within(sessionsCard()).findByRole("button", { name: "Sign out of all other sessions" }));
    fireEvent.click(await screen.findByRole("button", { name: "Sign out of the other sessions" }));
    await waitFor(() => expect(api.endOtherSessions).toHaveBeenCalledOnce());
  });

  it("alone, offers nothing to end", async () => {
    vi.mocked(api.mySessions).mockResolvedValue([SESSIONS[0]!]);
    wrap();
    await within(sessionsCard()).findByText("This session");
    expect(within(sessionsCard()).queryByRole("button", { name: "Sign out of all other sessions" })).toBeNull();
  });

  it("says why this browser is not listed when it signed in before sessions were recorded", async () => {
    vi.mocked(api.mySessions).mockResolvedValue([SESSIONS[1]!]);
    wrap();
    expect(await within(sessionsCard()).findByText(/signed in before sessions were listed/)).toBeTruthy();
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
