import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TopNav } from "@/app/top-nav";
import { personName } from "@/components/account/account-menu";
import { ThemeProvider } from "@/lib/theme";

/** Who is signed in is a menu under their name: account, tokens, theme, sign out. */
const session = vi.hoisted(() => ({
  me: null as null | Record<string, unknown>,
  deconnecter: vi.fn(),
}));
vi.mock("next/navigation", () => ({ usePathname: () => "/" }));
vi.mock("@/lib/session", () => ({
  useSession: () => ({ me: session.me, orgs: [], org: "acme", selectOrg: vi.fn(), deconnecter: session.deconnecter }),
}));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      edition: vi.fn().mockResolvedValue({ edition: "community", features: [] }),
      projects: vi.fn().mockResolvedValue({ items: [], meta: { has_more: false } }),
      workItems: vi.fn().mockResolvedValue({ items: [], meta: { has_more: false } }),
      orgActions: vi.fn().mockResolvedValue([]),
    },
  };
});

function wrap() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ThemeProvider>
        <TopNav />
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

const accountButton = () => screen.getByRole("button", { name: "Account of Vincent Devillers" });

describe("the account menu", () => {
  beforeEach(() => {
    session.me = { id: "u1", display_name: "Vincent Devillers", email: "vdevillers@diametral.test", memberships: [] };
    session.deconnecter.mockClear();
  });

  it("shows the person's name, not a menu's worth of controls, in the header", () => {
    wrap();
    expect(accountButton()).toHaveAttribute("aria-expanded", "false");
    expect(accountButton()).toHaveTextContent("Vincent Devillers");
    // The theme and the sign-out wait in the menu: the header no longer carries them loose.
    expect(screen.queryByRole("radio", { name: "Light" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Sign out" })).toBeNull();
  });

  it("opens on the account, the tokens, the theme and signing out", () => {
    wrap();
    fireEvent.click(accountButton());
    expect(accountButton()).toHaveAttribute("aria-expanded", "true");
    const panel = document.getElementById(accountButton().getAttribute("aria-controls")!)!;
    expect(within(panel).getByText("vdevillers@diametral.test")).toBeInTheDocument();
    expect(within(panel).getByRole("link", { name: "My account" })).toHaveAttribute("href", "/account");
    expect(within(panel).getByRole("link", { name: "My access tokens" })).toHaveAttribute("href", "/account#tokens");
    expect(within(panel).getByRole("radio", { name: "Light" })).toBeInTheDocument();
    fireEvent.click(within(panel).getByRole("button", { name: "Sign out" }));
    expect(session.deconnecter).toHaveBeenCalledOnce();
  });

  it("closes on Escape, giving the focus back, and on a click outside", () => {
    wrap();
    fireEvent.click(accountButton());
    fireEvent.keyDown(window, { key: "Escape" });
    expect(accountButton()).toHaveAttribute("aria-expanded", "false");
    expect(accountButton()).toHaveFocus();
    fireEvent.click(accountButton());
    fireEvent.pointerDown(document.body);
    expect(accountButton()).toHaveAttribute("aria-expanded", "false");
  });

  it("signed out: a sign-in link and the theme, no menu", () => {
    session.me = null;
    wrap();
    expect(screen.queryByRole("button", { name: /^Account of/ })).toBeNull();
    expect(screen.getAllByRole("link", { name: "Sign in" }).length).toBeGreaterThan(0);
    expect(screen.getByRole("radio", { name: "Light" })).toBeInTheDocument();
  });

  it("names a person by their name, else by their address", () => {
    expect(personName({ display_name: "Vincent Devillers", email: "v@x.test" })).toBe("Vincent Devillers");
    expect(personName({ display_name: "  ", email: "v@x.test" })).toBe("v@x.test");
  });
});
