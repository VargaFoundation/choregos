import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemberLinkedAccounts } from "@/components/accounts/member-linked-accounts";
import { MyLinkedAccountsCard, navigation } from "@/components/accounts/my-linked-accounts";
import { api, ApiError } from "@/lib/api";
import type { LinkedAccount } from "@/lib/types";

/** A member's accounts on code forges (S66-41, ADR 0161): linked by the member, or declared. */
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      myLinkedAccounts: vi.fn(),
      startMyLinkedAccount: vi.fn(),
      deleteMyLinkedAccount: vi.fn(),
      memberLinkedAccounts: vi.fn(),
      declareMemberLinkedAccount: vi.fn(),
      deleteMemberLinkedAccount: vi.fn(),
    },
  };
});

const VERIFIED: LinkedAccount = {
  id: "la-1",
  org: "acme",
  user_id: "u1",
  provider: "github",
  base_url: "https://github.com",
  external_id: "583231",
  login: "octocat",
  avatar_url: "https://avatars.githubusercontent.com/u/583231",
  profile_url: "https://github.com/octocat",
  verified: true,
  verified_at: "2026-11-02T09:00:00Z",
  connector: "github",
  created_by: "augustin@acme.dev",
  created_at: "2026-11-02T09:00:00Z",
};
const DECLARED: LinkedAccount = {
  ...VERIFIED,
  id: "la-2",
  provider: "gitlab",
  base_url: "https://gitlab.acme.example",
  external_id: "77",
  login: "octo",
  avatar_url: null,
  profile_url: null,
  verified: false,
  verified_at: null,
  connector: null,
  created_by: "admin@acme.dev",
};

function wrap(node: React.ReactNode) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{node}</QueryClientProvider>,
  );
}

describe("my linked accounts", () => {
  beforeEach(() => {
    cleanup();
    vi.resetAllMocks();
  });

  it("lists verified and declared accounts, and draws no image from a forge", async () => {
    vi.mocked(api.myLinkedAccounts).mockResolvedValue({ items: [VERIFIED, DECLARED], instances: [] });
    wrap(<MyLinkedAccountsCard search="" />);
    const list = await screen.findByRole("list", { name: "Your linked accounts" });
    const github = within(list).getByTestId("linked-github-583231");
    expect(within(github).getByText("Verified")).toBeTruthy();
    expect(within(github).getByText("GitHub")).toBeTruthy();
    expect(within(github).getByRole("link", { name: "octocat" }).getAttribute("href")).toBe("https://github.com/octocat");
    const gitlab = within(list).getByTestId("linked-gitlab-77");
    expect(within(gitlab).getByText("Declared")).toBeTruthy();
    expect(within(gitlab).getByText("GitLab · gitlab.acme.example")).toBeTruthy();
    expect(list.querySelector("img")).toBeNull();
  });

  it("starts the link at the forge the organisation configured", async () => {
    vi.mocked(api.myLinkedAccounts).mockResolvedValue({
      items: [],
      instances: [{ org: "acme", instance: "forge", provider: "forgejo", base_url: "https://forge.acme.example" }],
    });
    vi.mocked(api.startMyLinkedAccount).mockResolvedValue({ authorization_url: "https://forge.acme.example/login/oauth/authorize?x=1" });
    const assign = vi.spyOn(navigation, "assign").mockImplementation(() => undefined);
    wrap(<MyLinkedAccountsCard search="" />);
    fireEvent.click(await screen.findByRole("button", { name: "Link Forgejo · forge.acme.example" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://forge.acme.example/login/oauth/authorize?x=1"));
    expect(api.startMyLinkedAccount).toHaveBeenCalledWith("forgejo", "acme", "forge");
  });

  it("says what the callback answered", async () => {
    vi.mocked(api.myLinkedAccounts).mockResolvedValue({ items: [], instances: [] });
    wrap(<MyLinkedAccountsCard search="?link_error=taken" />);
    expect(await screen.findByText("That account is already linked to another member of the organisation.")).toBeTruthy();
    cleanup();
    wrap(<MyLinkedAccountsCard search="?linked=gitlab" />);
    expect(await screen.findByText("Your GitLab account is linked.")).toBeTruthy();
  });

  it("unlinks after a confirmation", async () => {
    vi.mocked(api.myLinkedAccounts).mockResolvedValue({ items: [VERIFIED], instances: [] });
    vi.mocked(api.deleteMyLinkedAccount).mockResolvedValue(undefined);
    wrap(<MyLinkedAccountsCard search="" />);
    fireEvent.click(await screen.findByRole("button", { name: "Unlink" }));
    const confirm = await screen.findAllByRole("button", { name: "Unlink" });
    fireEvent.click(confirm[confirm.length - 1]!);
    await waitFor(() => expect(api.deleteMyLinkedAccount).toHaveBeenCalledWith("la-1"));
  });
});

describe("a member's linked accounts, for an administrator", () => {
  beforeEach(() => {
    cleanup();
    vi.resetAllMocks();
  });

  it("declares an account by numeric id on any forge", async () => {
    vi.mocked(api.memberLinkedAccounts).mockResolvedValue([VERIFIED]);
    vi.mocked(api.declareMemberLinkedAccount).mockResolvedValue(DECLARED);
    wrap(<MemberLinkedAccounts org="acme" userId="u2" />);
    expect(await screen.findByText("Verified")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Forge"), { target: { value: "forgejo" } });
    fireEvent.change(screen.getByLabelText("Login"), { target: { value: "octo" } });
    fireEvent.change(screen.getByLabelText("User id"), { target: { value: "7a7" } });
    const declare = screen.getByRole("button", { name: "Declare" }) as HTMLButtonElement;
    expect(declare.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Instance"), { target: { value: "https://forge.acme.example" } });
    fireEvent.click(declare);
    await waitFor(() =>
      expect(api.declareMemberLinkedAccount).toHaveBeenCalledWith("acme", "u2", {
        provider: "forgejo",
        base_url: "https://forge.acme.example",
        external_id: "77",
        login: "octo",
      }),
    );
  });

  it("shows the conflict when the account belongs to another member", async () => {
    vi.mocked(api.memberLinkedAccounts).mockResolvedValue([]);
    vi.mocked(api.declareMemberLinkedAccount).mockRejectedValue(
      new ApiError(409, { title: "Conflict", detail: "this github user id is already linked to another member" }),
    );
    wrap(<MemberLinkedAccounts org="acme" userId="u2" />);
    fireEvent.change(await screen.findByLabelText("Login"), { target: { value: "mallory" } });
    fireEvent.change(screen.getByLabelText("User id"), { target: { value: "1001" } });
    fireEvent.click(screen.getByRole("button", { name: "Declare" }));
    expect(await screen.findByText("this github user id is already linked to another member")).toBeTruthy();
    expect(api.declareMemberLinkedAccount).toHaveBeenCalledWith("acme", "u2", {
      provider: "github",
      base_url: null,
      external_id: "1001",
      login: "mallory",
    });
  });
});
