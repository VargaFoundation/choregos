import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { peopleOf, PROJECT_ROLES } from "@/components/members";
import { MembersTable } from "@/components/members-table";
import { api } from "@/lib/api";
import type { Membership } from "@/lib/types";

/** Members per scope (S30-06, plan item 9): a person appears once; no `org_admin` on a project row. */
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme", me: { email: "augustin@acme.dev" } }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      members: vi.fn(),
      addMember: vi.fn().mockResolvedValue({}),
      projects: vi.fn().mockResolvedValue({
        items: [
          { slug: "billing-api", name: "Billing API" },
          { slug: "checkout-web", name: "Checkout Web" },
        ],
        meta: { has_more: false },
      }),
    },
  };
});

const M = (email: string, role: string, project_slug?: string): Membership =>
  ({ org: "acme", user_id: email, email, role, project_slug: project_slug ?? null }) as Membership;

const MEMBERSHIPS = [
  M("augustin@acme.dev", "org_admin"),
  M("lea@acme.dev", "developer"),
  M("lea@acme.dev", "project_owner", "billing-api"),
  M("marc@acme.dev", "developer", "checkout-web"),
];

function wrap(project?: string) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MembersTable project={project} />
    </QueryClientProvider>,
  );
}

const options = (select: HTMLElement) => within(select).getAllByRole("option").map((o) => o.getAttribute("value"));

describe("members per scope", () => {
  it("one row per person: their organisation role, and their projects", () => {
    const people = peopleOf(MEMBERSHIPS);
    expect(people.map((p) => p.email)).toEqual(["augustin@acme.dev", "lea@acme.dev", "marc@acme.dev"]);
    expect(people[1]).toMatchObject({ orgRole: "developer", projects: [{ slug: "billing-api", role: "project_owner" }] });
    expect(people[2]).toMatchObject({ orgRole: null, projects: [{ slug: "checkout-web", role: "developer" }] });
  });

  it("the organisation's page lists a person once, and offers a project picker", async () => {
    vi.mocked(api.members).mockResolvedValue(MEMBERSHIPS);
    wrap();
    const table = await screen.findByTestId("membres");
    expect(within(table).getAllByTestId("membre-lea@acme.dev")).toHaveLength(1);
    expect(within(table).getAllByRole("row")).toHaveLength(4);
    expect(screen.getByTestId("membre-lea@acme.dev")).toHaveTextContent("billing-api · Project owner");
    expect(api.members).toHaveBeenCalledWith("acme", undefined);
    // The scope of an invitation is a list of the organisation's projects, not free text.
    const scope = screen.getByLabelText("Scope");
    expect(await within(scope).findByRole("option", { name: "Checkout Web" })).toBeInTheDocument();
    expect(options(screen.getByLabelText("Role"))).toContain("org_admin");
    // A project chosen: org_admin is not offered.
    fireEvent.change(scope, { target: { value: "billing-api" } });
    expect(options(screen.getByLabelText("Role"))).not.toContain("org_admin");
    expect(screen.getByTestId("roles-additifs")).toHaveTextContent("adds to the person's organisation role");
  });

  it("a project's page lists its members with project roles only", async () => {
    vi.mocked(api.members).mockResolvedValue([M("lea@acme.dev", "project_owner", "billing-api")]);
    wrap("billing-api");
    const role = await screen.findByLabelText("Role of lea@acme.dev on billing-api");
    expect(role).toHaveValue("project_owner");
    expect(options(role)).not.toContain("org_admin");
    expect(options(screen.getByLabelText("Role"))).toEqual([...PROJECT_ROLES]);
    expect(screen.queryByLabelText("Scope")).toBeNull();
    expect(api.members).toHaveBeenCalledWith("acme", "billing-api");
  });
});
