// SPDX-License-Identifier: Apache-2.0
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SessionsCard } from "@/components/account/sessions";
import { AccessTokensCard } from "@/components/account/access-tokens";
import { OrgSwitcher } from "@/components/org-switcher";
import { api } from "@/lib/api";
import type { ApiToken, BrowserSession, Org } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, orgs: vi.fn(), myTokens: vi.fn(), mySessions: vi.fn() } };
});

const meta = (next: string | null) => ({ has_more: next !== null, next_cursor: next });
const org = (slug: string): Org => ({ slug, name: `Company ${slug}`, role: "developer", currency: "EUR" }) as Org;
const token = (id: string): ApiToken => ({ id, name: `token-${id}`, scopes: ["*"], created_at: "2026-11-01T00:00:00Z" }) as ApiToken;
const session = (id: string, current = false): BrowserSession =>
  ({ id, channel: "oidc", kind: "browser", created_at: "2026-11-01T00:00:00Z", expires_at: "2026-11-02T00:00:00Z", current }) as BrowserSession;

function show(node: React.ReactNode) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      {node}
    </QueryClientProvider>,
  );
}

describe("the organisation switcher asks the server (ADR 0206)", () => {
  beforeEach(() => vi.mocked(api.orgs).mockReset());

  it("is a plain select when the session holds every organisation", () => {
    show(<OrgSwitcher org="a" orgs={[org("a"), org("b")]} more={false} onSelect={vi.fn()} />);
    expect(within(screen.getByLabelText("Current organisation")).getAllByRole("option")).toHaveLength(2);
    expect(screen.queryByLabelText("Find an organisation")).toBeNull();
    expect(api.orgs).not.toHaveBeenCalled();
  });

  it("searches by the server's q, never over the loaded page, and pages with More", async () => {
    vi.mocked(api.orgs).mockImplementation(async (params = {}) => {
      if (params.q === "zeta") return { items: [org("zeta-1")], meta: meta(null) };
      return params.cursor ? { items: [org("c")], meta: meta(null) } : { items: [org("a"), org("b")], meta: meta("cur-1") };
    });
    const onSelect = vi.fn();
    show(<OrgSwitcher org="a" orgs={[org("a")]} more onSelect={onSelect} />);
    const select = screen.getByLabelText("Current organisation");
    await waitFor(() => expect(within(select).getAllByRole("option")).toHaveLength(2));

    fireEvent.click(screen.getByRole("button", { name: "More" }));
    await waitFor(() => expect(within(select).getAllByRole("option")).toHaveLength(3));
    expect(api.orgs).toHaveBeenLastCalledWith({ q: undefined, cursor: "cur-1", limit: 50 });

    const search = screen.getByLabelText("Find an organisation");
    fireEvent.change(search, { target: { value: " zeta " } });
    fireEvent.submit(search.closest("form")!);
    await waitFor(() => expect(api.orgs).toHaveBeenLastCalledWith({ q: "zeta", cursor: undefined, limit: 50 }));
    // The current organisation stays selectable even though the search did not return it.
    await waitFor(() => expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["Company a", "Company zeta-1"]));
    fireEvent.change(select, { target: { value: "zeta-1" } });
    expect(onSelect).toHaveBeenCalledWith("zeta-1");
  });
});

describe("the account's token and session lists page by the server", () => {
  beforeEach(() => {
    vi.mocked(api.myTokens).mockReset();
    vi.mocked(api.mySessions).mockReset();
  });

  it("shows more tokens with More, each once", async () => {
    vi.mocked(api.myTokens).mockImplementation(async (params = {}) =>
      params.cursor ? { items: [token("3")], meta: meta(null) } : { items: [token("1"), token("2")], meta: meta("next") },
    );
    show(<AccessTokensCard />);
    expect(await screen.findAllByTestId("token")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    await waitFor(() => expect(screen.getAllByTestId("token")).toHaveLength(3));
    expect(screen.queryByRole("button", { name: "More" })).toBeNull();
    expect(api.myTokens).toHaveBeenLastCalledWith({ kind: undefined, q: undefined, cursor: "next", limit: 50 });
  });

  it("shows more sessions with More, and asks the server about the current and the others", async () => {
    vi.mocked(api.mySessions).mockImplementation(async (params = {}) => {
      if (params.current === true) return { items: [session("here", true)], meta: meta(null) };
      if (params.current === false) return { items: [session("s1")], meta: meta("x") };
      return params.cursor
        ? { items: [session("s2")], meta: meta(null) }
        : { items: [session("here", true), session("s1")], meta: meta("c1") };
    });
    show(<SessionsCard />);
    expect(await screen.findAllByTestId("session")).toHaveLength(2);
    // Others exist (asked of the server), so the gesture is offered even before the next page loads.
    expect(await screen.findByRole("button", { name: "Sign out of all other sessions" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    await waitFor(() => expect(screen.getAllByTestId("session")).toHaveLength(3));
    expect(api.mySessions).toHaveBeenCalledWith({ current: true, limit: 1 });
    expect(api.mySessions).toHaveBeenCalledWith({ current: false, limit: 2 });
  });
});
