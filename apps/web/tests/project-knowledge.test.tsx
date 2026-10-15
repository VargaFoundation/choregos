import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Suspense, act } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ProjectKnowledgePage from "@/app/p/[slug]/knowledge/page";
import { api } from "@/lib/api";
import { alsoServedToProject, ownedByProject, servedToProject, usedByLabel } from "@/lib/knowledge";
import type { KnowledgeBase } from "@/lib/types";

const push = vi.fn();
const session = vi.hoisted(() => ({
  me: { memberships: [{ org: "acme", role: "org_admin" }] } as { memberships: Array<Record<string, unknown>> },
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }), usePathname: () => "/p/billing-api/knowledge" }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme", me: session.me }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, knowledgeBases: vi.fn(), createProjectKnowledgeBase: vi.fn(), project: vi.fn() } };
});

const make = (slug: string, scope: KnowledgeBase["scope"], projects: string[] = [], owner?: string): KnowledgeBase => ({
  owner_project: owner ?? null,
  slug,
  name: `Base ${slug}`,
  scope,
  projects,
  groups: scope === "groups" ? ["auditors"] : [],
  serving_mode: "files",
  status: "active",
  document_count: 0,
  documents_by_status: {},
});
const orgBase = make("policy", "org");
const mine = make("runbooks", "projects", ["billing-api"], "billing-api");
const joint = make("shared-ops", "projects", ["billing-api", "checkout-web"]);
const theirs = make("checkout-docs", "projects", ["checkout-web"]);
const people = make("audit", "groups");

async function page() {
  await act(async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <Suspense fallback="…">
          <ProjectKnowledgePage params={Promise.resolve({ slug: "billing-api" })} />
        </Suspense>
      </QueryClientProvider>,
    );
  });
}

describe("what a project's runs are served", () => {
  it("an organisation-wide base, or one that names the project; never a group's, never another project's", () => {
    expect(servedToProject(orgBase, "billing-api")).toBe(true);
    expect(servedToProject(mine, "billing-api")).toBe(true);
    expect(servedToProject(theirs, "billing-api")).toBe(false);
    expect(servedToProject(people, "billing-api")).toBe(false);
    expect(ownedByProject(mine, "billing-api")).toBe(true);
    expect(ownedByProject(orgBase, "billing-api")).toBe(false);
    // Owned means created from the project: narrowing an organisation base to one project does not make it theirs.
    expect(ownedByProject(joint, "billing-api")).toBe(false);
    expect(alsoServedToProject(joint, "billing-api")).toBe(true);
    expect(alsoServedToProject(mine, "billing-api")).toBe(false);
  });

  it("says who uses a base, with the projects' names", () => {
    expect(usedByLabel(orgBase)).toBe("Every project");
    expect(usedByLabel(mine, { "billing-api": "Billing API" })).toBe("Billing API");
    expect(usedByLabel(people)).toBe("People in groups auditors (never agents)");
  });
});

describe("the project's Knowledge page (S39-14)", () => {
  beforeEach(() => {
    push.mockReset();
    session.me = { memberships: [{ org: "acme", role: "org_admin" }] };
    vi.mocked(api.project).mockResolvedValue({ slug: "billing-api", name: "Billing API" } as never);
    vi.mocked(api.knowledgeBases).mockResolvedValue([orgBase, mine, joint, theirs, people]);
    vi.mocked(api.createProjectKnowledgeBase).mockResolvedValue(make("handbook", "projects", ["billing-api"], "billing-api"));
  });

  it("lists the project's own bases and the shared ones, and nothing else", async () => {
    await page();
    const own = await screen.findByTestId("project-knowledge-own");
    expect(within(own).getByRole("link", { name: "Base runbooks" })).toHaveAttribute("href", "/p/billing-api/knowledge/runbooks");
    expect(own).not.toHaveTextContent("Base policy");
    expect(own).not.toHaveTextContent("Base shared-ops");
    expect(screen.getByTestId("project-knowledge-also")).toHaveTextContent("Base shared-ops");
    expect(screen.getByTestId("project-knowledge-shared")).toHaveTextContent("Base policy");
    expect(screen.queryByText("Base checkout-docs")).toBeNull();
    expect(screen.queryByText("Base audit")).toBeNull();
  });

  it("creates a base owned by the project, with no scope to choose, then opens it", async () => {
    await page();
    fireEvent.click(await screen.findByRole("button", { name: "New knowledge base" }));
    const form = screen.getByRole("dialog");
    expect(within(form).queryByText("Scope")).toBeNull();
    expect(form).toHaveTextContent("Served to the runs of Billing API only");
    fireEvent.change(within(form).getByPlaceholderText("runbooks"), { target: { value: "handbook" } });
    fireEvent.click(within(form).getByRole("button", { name: "Create" }));
    await waitFor(() => expect(api.createProjectKnowledgeBase).toHaveBeenCalled());
    const [project, body] = vi.mocked(api.createProjectKnowledgeBase).mock.calls[0]!;
    expect(project).toBe("billing-api");
    expect(body).toMatchObject({ slug: "handbook" });
    expect(body).not.toHaveProperty("scope");
    await waitFor(() => expect(push).toHaveBeenCalledWith("/p/billing-api/knowledge/handbook"));
  });

  it("offers the creation to an owner of this project, not to a developer or to another project's owner", async () => {
    session.me = { memberships: [{ org: "acme", role: "project_owner", project_slug: "billing-api" }] };
    await page();
    await screen.findByTestId("project-knowledge-own");
    expect(screen.getByRole("button", { name: "New knowledge base" })).toBeInTheDocument();
  });

  it.each([
    ["a developer of the project", { org: "acme", role: "developer", project_slug: "billing-api" }],
    ["the owner of another project", { org: "acme", role: "project_owner", project_slug: "checkout-web" }],
  ])("offers no creation to %s", async (_who, membership) => {
    session.me = { memberships: [membership] };
    await page();
    await screen.findByTestId("project-knowledge-own");
    expect(screen.queryByRole("button", { name: "New knowledge base" })).toBeNull();
  });
});
