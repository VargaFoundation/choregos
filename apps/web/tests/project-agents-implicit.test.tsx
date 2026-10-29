import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import { Suspense } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ProjectAgentsPage from "@/app/p/[slug]/agents/page";
import { api } from "@/lib/api";
import { builtinAgentDemo } from "@/mocks/data";

/**
 * A project whose workflows name no registry agent read "Project agents: none yet" at the top, and
 * its built-in agents were a name and a role at the bottom of the page: nothing said they run, nor
 * what. The empty sentence now points to them, and each says its prompt, model and tools.
 */
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme", me: { email: "lea@acme.dev", memberships: [] } }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      projectAgents: vi.fn().mockResolvedValue([]),
      agents: vi.fn().mockResolvedValue([]),
      implicitAgents: vi.fn(),
      projectAgentChannel: vi.fn().mockResolvedValue({ channel: "stable" }),
      describeBuiltinAgent: vi.fn(),
      workflowAgents: vi.fn(),
      resolveProjectAgents: vi.fn(),
    },
  };
});

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.implicitAgents).mockResolvedValue([
    { workflow: "default-simple", actor: "dev", role: "implement", model: "profile:by_size" },
    { workflow: "default-simple", actor: "checker", role: "verify", model: "profile:standard" },
  ]);
  vi.mocked(api.workflowAgents).mockResolvedValue([
    { workflow: "default-simple", actor: "dev", role: "implement", model: "profile:by_size", agent: null, registered: false },
    { workflow: "default-simple", actor: "reviewer", role: "review", agent: "reviewer", registered: true },
    { workflow: "default-simple", actor: "ghost", role: "review", agent: "ghost", registered: false },
  ]);
  vi.mocked(api.resolveProjectAgents).mockResolvedValue([
    { ref: "ghost", slug: "ghost", status: "not_installed", pinned: false, in_catalogue: false },
    { ref: "reviewer", slug: "reviewer", status: "resolved", version: 3, pinned: true, display_name: "Reviewer", in_catalogue: true },
  ]);
  vi.mocked(api.describeBuiltinAgent).mockImplementation(async (_slug, actor) =>
    builtinAgentDemo(`/x?role=${actor.role}&model=${actor.model}`),
  );
});

describe("the project's built-in agents", () => {
  it("are announced where the project's agents are said to be none, and each says what it runs", async () => {
    await act(async () => {
      render(
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <Suspense fallback="…">
            <ProjectAgentsPage params={Promise.resolve({ slug: "foundation" })} />
          </Suspense>
        </QueryClientProvider>,
      );
    });
    await waitFor(() =>
      expect(screen.getByTestId("project-owned-none")).toHaveTextContent("Its workflows still run 2 built-in agents"),
    );
    const list = await screen.findByTestId("implicites");
    await waitFor(() => expect(within(list).getAllByTestId("builtin-agent")).toHaveLength(2));
    expect(api.describeBuiltinAgent).toHaveBeenCalledWith("foundation", expect.objectContaining({ role: "implement" }));
    expect(within(list).getAllByTestId("builtin-agent-prompt")[0]).toHaveTextContent("You carry the implement role");
  });

  it("lists every agent the workflows run in one table: registry version, built-in, or a name the registry lacks", async () => {
    await act(async () => {
      render(
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <Suspense fallback="…">
            <ProjectAgentsPage params={Promise.resolve({ slug: "foundation" })} />
          </Suspense>
        </QueryClientProvider>,
      );
    });
    const table = await screen.findByTestId("agents-du-projet");
    await waitFor(() => expect(within(table).getByText(/v3 · pinned/)).toBeInTheDocument());
    const rows = within(table).getAllByRole("row").slice(1).map((r) => r.textContent);
    expect(rows[0]).toContain("built-in agent · implement playbook");
    expect(rows[1]).toContain("Reviewer");
    expect(rows[2]).toContain("names ghost, which is not in the registry");
  });
});
