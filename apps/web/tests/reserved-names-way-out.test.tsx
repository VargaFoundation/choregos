import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Suspense } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import WorkflowsPage from "@/app/p/[slug]/workflows/page";
import SkillsPage from "@/app/skills/page";
import { routingRenamed } from "@/components/workflows/rename";
import { api } from "@/lib/api";
import { skillHref } from "@/lib/skill-slug";

/**
 * A workflow or a skill published under a name a console page took before S55-15 / S55-17 (S55-18):
 * its own page is that console page, so the list gives it a way out without opening it.
 */

const { search } = vi.hoisted(() => ({ search: { value: "" } }));

vi.mock("next/navigation", () => ({
  usePathname: () => "/skills",
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(search.value),
}));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme", me: { email: "ada@acme.dev" } }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      workflows: vi.fn(async () => [
        { name: "library", version: 3, is_default: true, open_items: 2, can_share: false },
        { name: "offboarding", version: 1, is_default: false, open_items: 0, can_share: false },
      ]),
      workflowRouting: vi.fn(async () => ({
        default: "library",
        rules: [{ workflow: "library", when: { labels_any: ["hr"] } }],
        strategy: "labels",
        hints: { library: "arrivals" },
      })),
      project: vi.fn(async () => ({ id: "p", slug: "hr", org: "acme", status: "active", config: {} })),
      workflowNamed: vi.fn(async () => ({
        name: "library",
        version: 3,
        source: "platform",
        yaml: "apiVersion: choregos/v1\nmetadata:\n  name: library\nstates: {}\n",
        checksum: "x",
        is_active: true,
      })),
      createWorkflow: vi.fn(async () => ({})),
      putWorkflowRouting: vi.fn(async (_: string, routing: unknown) => routing),
      deactivateWorkflow: vi.fn(async () => undefined),
      skills: vi.fn(async () => [
        { slug: "library", description: "Published before S55-17", latest_version: 1, used_by: [] },
        { slug: "pdf", description: "PDF files", latest_version: 2, used_by: [] },
      ]),
      skill: vi.fn(async (_: string, slug: string) => ({
        slug,
        description: "Published before S55-17",
        latest_version: 1,
        versions: [{ version: 1 }],
        used_by: ["writer@1"],
      })),
      skillVersion: vi.fn(async () => ({
        version: 1,
        digest: "sha256:" + "a".repeat(64),
        files: { "SKILL.md": "---\nname: library\n---\nHow to shelve a book.\n" },
        risk: "low",
        review_status: "approved",
      })),
    },
  };
});

function wrap(children: React.ReactNode) {
  return (
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <Suspense fallback="…">{children}</Suspense>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  search.value = "";
  vi.clearAllMocks();
});

describe("a workflow under a console page's name is renamed from the list (S55-18)", () => {
  it("is not linked to its hidden page, says why, and offers a rename", async () => {
    await act(async () => {
      render(wrap(<WorkflowsPage params={Promise.resolve({ slug: "hr" })} />));
    });
    await screen.findByTestId("workflow-card-library");
    const cards = screen.getByRole("list", { name: "Workflows" });
    const hrefs = within(cards)
      .getAllByRole("link")
      .map((link) => link.getAttribute("href"));
    expect(hrefs).toContain("/p/hr/workflows/offboarding");
    expect(hrefs).not.toContain("/p/hr/workflows/library");
    expect(screen.getByTestId("workflow-hidden-library")).toHaveTextContent(
      "This name is now used by a console page: rename it to open it",
    );
    expect(screen.queryByTestId("workflow-hidden-offboarding")).toBeNull();
  });

  it("refuses a reserved or taken name, then publishes the copy, moves the routing and deactivates the old one", async () => {
    await act(async () => {
      render(wrap(<WorkflowsPage params={Promise.resolve({ slug: "hr" })} />));
    });
    const form = await screen.findByRole("form", { name: "Rename library" });
    const input = within(form).getByLabelText("New name");
    const button = within(form).getByRole("button", { name: "Rename library" });
    fireEvent.change(input, { target: { value: "new" } });
    expect(within(form).getByText(/new is reserved/)).toBeInTheDocument();
    expect(button).toBeDisabled();
    fireEvent.change(input, { target: { value: "offboarding" } });
    expect(within(form).getByText("A workflow named offboarding already exists")).toBeInTheDocument();
    expect(button).toBeDisabled();
    fireEvent.change(input, { target: { value: "arrivals" } });
    fireEvent.click(button);
    await waitFor(() => expect(api.deactivateWorkflow).toHaveBeenCalledWith("hr", "library"));
    expect(api.createWorkflow).toHaveBeenCalledWith(
      "hr",
      "arrivals",
      "apiVersion: choregos/v1\nmetadata:\n  name: arrivals\nstates: {}\n",
    );
    expect(api.putWorkflowRouting).toHaveBeenCalledWith("hr", {
      default: "arrivals",
      rules: [{ workflow: "arrivals", when: { labels_any: ["hr"] } }],
      strategy: "labels",
      hints: { arrivals: "arrivals" },
    });
    // In that order: the copy exists before the routing names it, and the routing moved before the deactivation.
    const order = (fn: unknown) => vi.mocked(fn as () => void).mock.invocationCallOrder[0]!;
    expect(order(api.createWorkflow)).toBeLessThan(order(api.putWorkflowRouting));
    expect(order(api.putWorkflowRouting)).toBeLessThan(order(api.deactivateWorkflow));
  });

  it("leaves a routing that never names the workflow unsaved", () => {
    expect(routingRenamed({ default: "a", rules: [] }, "library", "b")).toBeNull();
    expect(routingRenamed({ default: "a", rules: [], hints: { library: "x", a: "y" } }, "library", "b")).toEqual({
      default: "a",
      rules: [],
      hints: { a: "y", b: "x" },
    });
  });
});

describe("a skill under a console page's slug opens on the list (S55-18)", () => {
  it("links to the list's view of it, never to its hidden page, and says why", async () => {
    expect(skillHref("library")).toBe("/skills?skill=library");
    expect(skillHref("catalogue")).toBe("/skills?skill=catalogue");
    expect(skillHref("pdf")).toBe("/skills/pdf");
    await act(async () => {
      render(wrap(<SkillsPage />));
    });
    const table = await screen.findByTestId("installed-skills");
    expect(within(table).getByRole("link", { name: "library" })).toHaveAttribute("href", "/skills?skill=library");
    expect(within(table).getByRole("link", { name: "pdf" })).toHaveAttribute("href", "/skills/pdf");
    expect(screen.getByTestId("skill-hidden-library")).toHaveTextContent("This name is now used by a console page");
    expect(screen.queryByTestId("skill-hidden-pdf")).toBeNull();
  });

  it("?skill= shows the skill, its files and who carries it, in place of the list", async () => {
    search.value = "skill=library";
    await act(async () => {
      render(wrap(<SkillsPage />));
    });
    expect(await screen.findByRole("heading", { name: "library" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText("SKILL.md")).toHaveTextContent("How to shelve a book."));
    expect(screen.getByRole("link", { name: "writer@1" })).toBeInTheDocument();
    expect(api.skill).toHaveBeenCalledWith("acme", "library");
    expect(screen.queryByTestId("installed-skills")).toBeNull();
  });
});
