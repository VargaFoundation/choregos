import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Avatar } from "@/components/accounts/avatar";
import { MemberProfileView } from "@/components/member-profile";
import { api, ApiError } from "@/lib/api";
import type { MemberProfile } from "@/lib/types";

/** A member's profile page (S66-42, ADR 0162): one read, what the caller may see. */
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      memberProfile: vi.fn(),
      memberLinkedAccounts: vi.fn().mockResolvedValue([]),
    },
  };
});

const BASIC: MemberProfile = {
  user_id: "u2",
  org: "acme",
  view: "basic",
  status: "active",
  display_name: "Léa",
  given_name: "Léa",
  family_name: "Martin",
  job_title: "Release manager",
  department: "Payments",
  email: "lea@acme.dev",
  avatar_url: "/api/v1/users/u2/avatar?v=3f2a",
  org_role: "developer",
  project_roles: [{ project_slug: "billing-api", project_name: "Billing API", role: "project_owner" }],
  groups: [
    { slug: "platform-team", name: "Platform team", source: "internal" },
    { slug: "payments", name: "Payments", source: "scim" },
    { slug: "release-captains", name: "release-captains", source: "identity_provider" },
  ],
  origin: null,
  last_login_at: null,
  sessions: null,
  work: null,
  administration: null,
};

const FULL: MemberProfile = {
  ...BASIC,
  view: "full",
  origin: "identity_provider",
  last_login_at: "2026-11-14T08:00:00Z",
  sessions: { open: 2, last_activity_at: "2026-11-14T09:00:00Z" },
  work: {
    pending_requests: [
      {
        id: "hr-1",
        work_item_id: "w1",
        work_item_key: "BILL-42",
        work_item_title: "Refund an invoice",
        project_slug: "billing-api",
        kind: "approval",
        requested_at: "2026-11-14T07:00:00Z",
        due_at: null,
        via: "person",
        group: null,
      },
      {
        id: "hr-2",
        work_item_id: "w2",
        work_item_key: "BILL-43",
        work_item_title: "Export as PDF",
        project_slug: "billing-api",
        kind: "approval",
        requested_at: "2026-11-14T06:00:00Z",
        due_at: null,
        via: "group",
        group: "release-captains",
      },
    ],
    decisions: [
      {
        request_id: "hr-0",
        work_item_id: "w3",
        work_item_key: "BILL-41",
        work_item_title: "Rate limit",
        project_slug: "billing-api",
        kind: "approval",
        approved: false,
        decided_at: "2026-11-13T07:00:00Z",
      },
    ],
    runs: [
      {
        id: "r3",
        work_item_id: "w1",
        work_item_key: "BILL-42",
        project_slug: "billing-api",
        stage_role: "implement",
        status: "succeeded",
        started_at: "2026-11-14T07:30:00Z",
        cost_usd: 0.84,
      },
    ],
    spend_month_usd: 12.4,
  },
  administration: {
    api_tokens: 3,
    audit: [
      {
        id: "a-1",
        actor_id: "u1",
        actor_kind: "user",
        action: "member.add",
        target_type: "user",
        target_id: "u2",
        payload: {},
        ts: "2026-11-01T07:00:00Z",
      },
    ],
  },
};

function wrap(props: { canManage?: boolean; isSelf?: boolean } = {}) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemberProfileView org="acme" userId="u2" canManage={props.canManage ?? false} isSelf={props.isSelf ?? false} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.mocked(api.memberProfile).mockReset();
});

describe("a member's profile", () => {
  it("shows an administrator everything: account, work, linked accounts and administration", async () => {
    vi.mocked(api.memberProfile).mockResolvedValue(FULL);
    wrap({ canManage: true });
    const header = await screen.findByTestId("profile-header");
    expect(within(header).getByText("Léa Martin")).toBeTruthy();
    expect(within(header).getByText("Release manager · Payments")).toBeTruthy();
    expect(within(header).getByText("lea@acme.dev")).toBeTruthy();
    expect(within(header).getByTestId("profile-status").textContent).toBe("Active");
    expect(within(header).getByText("Identity provider sign-in")).toBeTruthy();
    expect(within(header).getByTestId("profile-sessions").textContent).toBe("2");
    // The photo is the one served on this origin.
    expect(within(header).getByTestId("avatar-photo").getAttribute("src")).toBe("/api/v1/users/u2/avatar?v=3f2a");

    const groups = screen.getAllByTestId("profile-group");
    expect(groups.map((g) => within(g).getByTestId("group-source").textContent)).toEqual([
      "Internal",
      "Identity provider (SCIM)",
      "Identity provider (sign-in)",
    ]);
    expect(within(groups[0]!).getByRole("link", { name: "Platform team" }).getAttribute("href")).toBe("/admin/groups/platform-team");
    expect(within(groups[2]!).queryByRole("link")).toBeNull();
    expect(screen.getByRole("link", { name: "Billing API" }).getAttribute("href")).toBe("/p/billing-api");

    const pending = screen.getAllByTestId("profile-pending");
    expect(pending).toHaveLength(2);
    expect(within(pending[0]!).getByText("To them")).toBeTruthy();
    expect(pending[1]!.textContent).toContain("Via release-captains");
    expect(within(pending[0]!).getByRole("link", { name: "BILL-42" }).getAttribute("href")).toBe("/p/billing-api/items/w1");
    expect(within(screen.getByTestId("profile-decision")).getByText("Rejected")).toBeTruthy();
    expect(within(screen.getByTestId("profile-run")).getByRole("link", { name: "r3" }).getAttribute("href")).toBe("/p/billing-api/runs/r3");
    expect(screen.getByTestId("profile-spend").textContent).toContain("US$12.40");

    const administration = screen.getByTestId("profile-administration");
    expect(within(administration).getByTestId("profile-tokens").textContent).toBe("3");
    expect(within(administration).getByText("member.add")).toBeTruthy();
    expect(within(administration).getByText("Target")).toBeTruthy();
    // An administrator declares linked accounts here.
    const linked = screen.getByTestId("profile-linked-accounts");
    expect(await within(linked).findByRole("button", { name: "Declare" })).toBeTruthy();
  });

  it("shows another member the card alone: no account, work, linked accounts or administration", async () => {
    vi.mocked(api.memberProfile).mockResolvedValue(BASIC);
    wrap();
    await screen.findByTestId("profile-header");
    expect(screen.getByText("Léa Martin")).toBeTruthy();
    expect(screen.getAllByTestId("profile-group")).toHaveLength(3);
    expect(screen.queryByTestId("profile-account")).toBeNull();
    expect(screen.queryByTestId("profile-work")).toBeNull();
    expect(screen.queryByTestId("profile-administration")).toBeNull();
    expect(screen.queryByTestId("profile-linked-accounts")).toBeNull();
    expect(api.memberLinkedAccounts).not.toHaveBeenCalled();
  });

  it("shows the person their own linked accounts, read-only", async () => {
    vi.mocked(api.memberProfile).mockResolvedValue({ ...FULL, administration: null });
    wrap({ isSelf: true });
    const linked = await screen.findByTestId("profile-linked-accounts");
    expect(within(linked).queryByRole("button", { name: "Declare" })).toBeNull();
    expect(within(linked).getByRole("link", { name: "My account" }).getAttribute("href")).toBe("/account#linked-accounts");
    expect(screen.queryByTestId("profile-administration")).toBeNull();
  });

  it("says when a person has left the organisation", async () => {
    vi.mocked(api.memberProfile).mockResolvedValue({ ...FULL, status: "former", org_role: null, groups: [] });
    wrap({ canManage: true });
    expect((await screen.findByTestId("profile-status")).textContent).toBe("No longer in the organisation");
    expect(screen.queryByTestId("profile-linked-accounts")).toBeNull();
  });

  it("says when the person is not a member", async () => {
    vi.mocked(api.memberProfile).mockRejectedValue(new ApiError(404, { title: "Not Found", detail: "Member u2 not found" }));
    wrap();
    expect(await screen.findByText("This person is not a member of acme.")).toBeTruthy();
  });

  it("says when the caller is not in the organisation", async () => {
    vi.mocked(api.memberProfile).mockRejectedValue(new ApiError(403, { title: "Forbidden", detail: "no" }));
    wrap();
    expect(await screen.findByText(/requires being a member of acme/)).toBeTruthy();
  });
});

describe("a person's avatar", () => {
  it("shows the initials without a photo", () => {
    render(<Avatar name="Léa Martin" />);
    expect(screen.getByTestId("avatar-initials").textContent).toBe("LM");
  });
});
