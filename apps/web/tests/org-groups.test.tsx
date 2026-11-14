// SPDX-License-Identifier: Apache-2.0
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Suspense } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import GroupPage from "@/app/admin/groups/[slug]/page";
import GroupsPage from "@/app/admin/groups/page";
import { adminGroups, sectionOf } from "@/components/shell/navigation";
import { api, ApiError } from "@/lib/api";
import type { Membership, OrgGroup, OrgGroupMember } from "@/lib/types";

const push = vi.hoisted(() => vi.fn());
const session = vi.hoisted(() => ({ memberships: [] as Membership[] }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme", me: { memberships: session.memberships } }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      orgGroups: vi.fn(),
      createOrgGroup: vi.fn(),
      orgGroup: vi.fn(),
      updateOrgGroup: vi.fn(),
      deleteOrgGroup: vi.fn(),
      orgGroupMembers: vi.fn(),
      addOrgGroupMember: vi.fn(),
      removeOrgGroupMember: vi.fn(),
      members: vi.fn(),
    },
  };
});

const ADMIN = [{ org: "acme", project_slug: null, role: "org_admin", user_id: "u-admin", email: "ada@acme.dev" }] as Membership[];
const DEVELOPER = [{ org: "acme", project_slug: null, role: "developer", user_id: "u-dev", email: "dev@acme.dev" }] as Membership[];

const group = (changes: Partial<OrgGroup> = {}): OrgGroup => ({
  slug: "release-captains",
  name: "Release captains",
  description: "Who ships",
  source: "internal",
  member_count: 1,
  created_by: "ada@acme.dev",
  created_at: "2026-11-12T09:00:00Z",
  ...changes,
});

const member = (email: string, userId: string): OrgGroupMember => ({
  user_id: userId,
  email,
  display_name: email.split("@")[0]!,
  added_by: "ada@acme.dev",
  added_at: "2026-11-12T09:30:00Z",
});

function client() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function list() {
  return render(
    <QueryClientProvider client={client()}>
      <GroupsPage />
    </QueryClientProvider>,
  );
}

async function one(slug = "release-captains") {
  const params = Promise.resolve({ slug });
  await act(async () => {
    render(
      <QueryClientProvider client={client()}>
        <Suspense fallback={null}>
          <GroupPage params={params} />
        </Suspense>
      </QueryClientProvider>,
    );
    await params;
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  session.memberships = ADMIN;
});

describe("the Groups page (ADR 0160)", () => {
  it("lists each group with its source and how many members it holds", async () => {
    vi.mocked(api.orgGroups).mockResolvedValue({
      items: [group(), group({ slug: "platform-team", name: "Platform team", source: "scim", member_count: 4, description: null })],
    });
    list();
    const captains = within(await screen.findByTestId("group-release-captains"));
    expect(api.orgGroups).toHaveBeenCalledWith("acme");
    expect(captains.getByRole("link", { name: "Release captains" })).toHaveAttribute("href", "/admin/groups/release-captains");
    expect(captains.getByText("Internal")).toBeInTheDocument();
    expect(captains.getByTestId("member-count")).toHaveTextContent("1");
    const platform = within(screen.getByTestId("group-platform-team"));
    expect(platform.getByText("Identity provider (SCIM)")).toBeInTheDocument();
    expect(platform.getByTestId("member-count")).toHaveTextContent("4");
  });

  it("an administrator creates an internal group and lands on its page", async () => {
    vi.mocked(api.orgGroups).mockResolvedValue({ items: [] });
    vi.mocked(api.createOrgGroup).mockResolvedValue(group({ member_count: 0 }));
    list();
    expect(await screen.findByText("No group yet")).toBeInTheDocument();
    const create = screen.getByRole("button", { name: "Create the group" });
    fireEvent.change(screen.getByLabelText("Slug"), { target: { value: "Release Captains" } });
    expect(create).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Slug"), { target: { value: "release-captains" } });
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: " Release captains " } });
    fireEvent.click(create);
    await waitFor(() => expect(api.createOrgGroup).toHaveBeenCalledWith("acme", { slug: "release-captains", name: "Release captains" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/admin/groups/release-captains"));
  });

  it("a member who is not the administrator reads the groups and is offered no creation", async () => {
    session.memberships = DEVELOPER;
    vi.mocked(api.orgGroups).mockResolvedValue({ items: [group()] });
    list();
    expect(await screen.findByTestId("group-release-captains")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create the group" })).toBeNull();
  });

  it("is an entry of the administration, current on a group's page too", () => {
    const groups = adminGroups([]);
    expect(sectionOf("/admin/groups", groups)?.label).toBe("Groups");
    expect(sectionOf("/admin/groups/release-captains", groups)?.label).toBe("Groups");
  });
});

describe("one group's page", () => {
  it("adds a member chosen among the organisation's members, and removes one", async () => {
    vi.mocked(api.orgGroup).mockResolvedValue(group());
    vi.mocked(api.orgGroupMembers).mockResolvedValue({ items: [member("cap@acme.dev", "u-cap")] });
    vi.mocked(api.members).mockResolvedValue([
      { org: "acme", project_slug: null, role: "developer", user_id: "u-cap", email: "cap@acme.dev" },
      { org: "acme", project_slug: null, role: "developer", user_id: "u-dev", email: "dev@acme.dev" },
      { org: "acme", project_slug: "billing", role: "viewer", user_id: "u-view", email: "view@acme.dev" },
    ] as Membership[]);
    vi.mocked(api.addOrgGroupMember).mockResolvedValue(member("dev@acme.dev", "u-dev"));
    vi.mocked(api.removeOrgGroupMember).mockResolvedValue(undefined);
    await one();

    expect(await screen.findByTestId("group-member-cap@acme.dev")).toBeInTheDocument();
    const choice = await screen.findByTestId("member-choice");
    await waitFor(() => expect(within(choice).getAllByRole("option").map((o) => o.textContent)).toEqual(["Choose a person…", "dev@acme.dev", "view@acme.dev"]));
    fireEvent.change(choice, { target: { value: "u-dev" } });
    fireEvent.click(screen.getByRole("button", { name: "Add to the group" }));
    await waitFor(() => expect(api.addOrgGroupMember).toHaveBeenCalledWith("acme", "release-captains", { user_id: "u-dev" }));

    fireEvent.click(screen.getByRole("button", { name: "Remove cap@acme.dev" }));
    await waitFor(() => expect(api.removeOrgGroupMember).toHaveBeenCalledWith("acme", "release-captains", "u-cap"));
  });

  it("says why a person was refused, in the API's words", async () => {
    vi.mocked(api.orgGroup).mockResolvedValue(group());
    vi.mocked(api.orgGroupMembers).mockResolvedValue({ items: [] });
    vi.mocked(api.members).mockResolvedValue([{ org: "acme", project_slug: null, role: "developer", user_id: "u-dev", email: "dev@acme.dev" }] as Membership[]);
    vi.mocked(api.addOrgGroupMember).mockRejectedValue(new ApiError(422, { detail: "`u-dev` is not a member of acme: only its members join its groups" }));
    await one();
    expect(await screen.findByText("Nobody in this group")).toBeInTheDocument();
    const choice = await screen.findByTestId("member-choice");
    await waitFor(() => expect(within(choice).getAllByRole("option")).toHaveLength(2));
    fireEvent.change(choice, { target: { value: "u-dev" } });
    fireEvent.click(screen.getByRole("button", { name: "Add to the group" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("only its members join its groups");
  });

  it("a group the identity provider pushes is read-only, and says so", async () => {
    vi.mocked(api.orgGroup).mockResolvedValue(group({ slug: "platform-team", name: "Platform team", source: "scim" }));
    vi.mocked(api.orgGroupMembers).mockResolvedValue({ items: [member("cap@acme.dev", "u-cap")] });
    await one("platform-team");
    expect(await screen.findByTestId("group-read-only")).toHaveTextContent("comes from the identity provider");
    expect(await screen.findByTestId("group-member-cap@acme.dev")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Remove/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
    expect(screen.queryByTestId("member-choice")).toBeNull();
    expect(api.members).not.toHaveBeenCalled();
  });

  it("renames an internal group, and deletes it after a confirmation", async () => {
    vi.mocked(api.orgGroup).mockResolvedValue(group());
    vi.mocked(api.orgGroupMembers).mockResolvedValue({ items: [] });
    vi.mocked(api.members).mockResolvedValue([]);
    vi.mocked(api.updateOrgGroup).mockResolvedValue(group({ name: "Captains" }));
    vi.mocked(api.deleteOrgGroup).mockResolvedValue(undefined);
    await one();
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Captains" } });
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.updateOrgGroup).toHaveBeenCalledWith("acme", "release-captains", { name: "Captains", description: null }));

    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete the group" }));
    await waitFor(() => expect(api.deleteOrgGroup).toHaveBeenCalledWith("acme", "release-captains"));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/admin/groups"));
  });

  it("a developer reads who is in an internal group and changes nothing", async () => {
    session.memberships = DEVELOPER;
    vi.mocked(api.orgGroup).mockResolvedValue(group());
    vi.mocked(api.orgGroupMembers).mockResolvedValue({ items: [member("cap@acme.dev", "u-cap")] });
    await one();
    expect(await screen.findByTestId("group-member-cap@acme.dev")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
    expect(screen.queryByTestId("member-choice")).toBeNull();
    expect(screen.queryByRole("button", { name: /Remove/ })).toBeNull();
  });
});
