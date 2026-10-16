import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Suspense, act } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import MemoriesPage from "@/app/memories/page";
import MemoryPage from "@/app/memories/[slug]/page";
import ProjectMemoryPage from "@/app/p/[slug]/memory/page";
import { api } from "@/lib/api";
import type { Memory, MemoryStore } from "@/lib/types";

/** The memories pages (S40-11, ADR 0075): the organisation's registry and page, and the project's selector. */
const session = vi.hoisted(() => ({
  me: { memberships: [{ org: "acme", role: "org_admin" }] } as { memberships: Array<Record<string, unknown>> },
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), usePathname: () => "/memories" }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme", me: session.me }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      memories: vi.fn(),
      memory: vi.fn(),
      createGlobalMemory: vi.fn(),
      updateMemory: vi.fn(),
      searchGlobalMemory: vi.fn(),
      globalMemoryPending: vi.fn(),
      decideGlobalMemory: vi.fn(),
      projects: vi.fn(),
      projectMemories: vi.fn(),
      createProjectMemory: vi.fn(),
      memorySearch: vi.fn(),
      memoryPending: vi.fn(),
      memoryDecide: vi.fn(),
      agents: vi.fn(),
    },
  };
});

const store = (extra: Partial<MemoryStore>): MemoryStore => ({
  slug: "security-lessons",
  display_name: "Security lessons",
  scope: "global",
  reach: "org",
  projects: [],
  status: "active",
  implicit: false,
  used_by: [],
  ...extra,
});
const SECURITY = store({ used_by: [{ agent: "architect", version: 3 }] });
const ARCH = store({ slug: "architecture-decisions", display_name: "Architecture decisions", reach: "projects", projects: ["billing-api"] });
const DECISIONS = store({ slug: "decisions", display_name: "Decisions", scope: "project", project: "billing-api", reach: null, projects: ["billing-api"] });
const proposal: Memory = { id: "g1", kind: "run_lesson", subject: "tls", content: "Pin TLS 1.2.", status: "pending", proposed_by: "r7" };

function wrap(node: React.ReactNode) {
  return (
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <Suspense fallback="…">{node}</Suspense>
    </QueryClientProvider>
  );
}
async function show(node: React.ReactNode) {
  await act(async () => {
    render(wrap(node));
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  session.me = { memberships: [{ org: "acme", role: "org_admin" }] };
  vi.mocked(api.memories).mockResolvedValue([SECURITY, ARCH]);
  vi.mocked(api.projects).mockResolvedValue({ items: [{ slug: "billing-api", name: "Billing API" }, { slug: "checkout-web", name: "Checkout Web" }], meta: { has_more: false } } as never);
  vi.mocked(api.agents).mockResolvedValue([{ slug: "architect", display_name: "Architect" }] as never);
});

describe("the organisation's registry", () => {
  it("lists the global memories with their reach, status and the agents that use them", async () => {
    await show(<MemoriesPage />);
    const table = await screen.findByTestId("memories");
    expect(within(table).getByRole("link", { name: "Security lessons" })).toHaveAttribute("href", "/memories/security-lessons");
    expect(within(table).getByText("every project")).toBeInTheDocument();
    expect(within(table).getByText("projects billing-api")).toBeInTheDocument();
    expect(within(table).getByRole("link", { name: "architect v3" })).toHaveAttribute("href", "/agents/architect");
    expect(api.memories).toHaveBeenCalledWith("acme", { scope: "global" });
  });

  it("an administrator creates one, with a reach", async () => {
    vi.mocked(api.createGlobalMemory).mockResolvedValue(SECURITY);
    await show(<MemoriesPage />);
    fireEvent.click(await screen.findByRole("button", { name: "New memory" }));
    const drawer = await screen.findByRole("dialog");
    fireEvent.change(within(drawer).getByLabelText("Slug"), { target: { value: "lessons-two" } });
    fireEvent.change(within(drawer).getByLabelText("Reach"), { target: { value: "projects" } });
    fireEvent.click(within(drawer).getByLabelText("Checkout Web"));
    fireEvent.click(within(drawer).getByRole("button", { name: "Create" }));
    await waitFor(() =>
      expect(api.createGlobalMemory).toHaveBeenCalledWith("acme", {
        slug: "lessons-two",
        display_name: "lessons-two",
        description: undefined,
        reach: "projects",
        projects: ["checkout-web"],
      }),
    );
  });

  it("offers nobody but an administrator the creation", async () => {
    session.me = { memberships: [{ org: "acme", role: "developer" }] };
    await show(<MemoriesPage />);
    await screen.findByTestId("memories");
    expect(screen.queryByRole("button", { name: "New memory" })).toBeNull();
  });
});

describe("one global memory", () => {
  beforeEach(() => {
    vi.mocked(api.memory).mockResolvedValue(SECURITY);
    vi.mocked(api.globalMemoryPending).mockResolvedValue([proposal]);
    vi.mocked(api.searchGlobalMemory).mockResolvedValue([{ id: "m1", kind: "decision", subject: "tls-min", content: "TLS 1.3 minimum.", status: "active" }]);
    vi.mocked(api.decideGlobalMemory).mockResolvedValue({});
    vi.mocked(api.updateMemory).mockResolvedValue(SECURITY);
  });
  const open = () => show(<MemoryPage params={Promise.resolve({ slug: "security-lessons" })} />);

  it("searches it, and decides what agents proposed", async () => {
    await open();
    fireEvent.change(await screen.findByLabelText("Search this memory"), { target: { value: "tls" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByText("TLS 1.3 minimum.")).toBeInTheDocument();
    expect(api.searchGlobalMemory).toHaveBeenCalledWith("acme", "security-lessons", "tls");
    const proposals = await screen.findByTestId("memory-proposals");
    fireEvent.click(within(proposals).getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(api.decideGlobalMemory).toHaveBeenCalledWith("acme", "security-lessons", "g1", "accept"));
  });

  it("edits its reach with a project picker, and archives it", async () => {
    await open();
    fireEvent.change(await screen.findByLabelText("Reach"), { target: { value: "projects" } });
    fireEvent.click(screen.getByLabelText("Billing API"));
    fireEvent.click(screen.getByRole("button", { name: "Save the reach" }));
    await waitFor(() => expect(api.updateMemory).toHaveBeenCalledWith("acme", "security-lessons", { reach: "projects", projects: ["billing-api"] }));
    fireEvent.click(screen.getByRole("button", { name: "Archive" }));
    await waitFor(() => expect(api.updateMemory).toHaveBeenCalledWith("acme", "security-lessons", { status: "archived" }));
    expect(screen.getByRole("link", { name: "architect v3" })).toBeInTheDocument();
  });

  it("shows a member neither the proposals nor the controls", async () => {
    session.me = { memberships: [{ org: "acme", role: "developer" }] };
    await open();
    await screen.findByText(/An organisation administrator decides what agents propose/);
    expect(api.globalMemoryPending).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Save the reach" })).toBeNull();
  });
});

describe("a project's memories", () => {
  const IMPLICIT = [
    store({ slug: "shared", display_name: "Shared memory", scope: "project", implicit: true, reach: null }),
    store({ slug: "own", display_name: "Own memory", scope: "project", implicit: true, reach: null }),
  ];
  beforeEach(() => {
    vi.mocked(api.projectMemories).mockResolvedValue([...IMPLICIT, DECISIONS, SECURITY]);
    vi.mocked(api.memorySearch).mockResolvedValue([]);
    vi.mocked(api.memoryPending).mockResolvedValue([proposal]);
    vi.mocked(api.memoryDecide).mockResolvedValue({});
    vi.mocked(api.searchGlobalMemory).mockResolvedValue([]);
    vi.mocked(api.createProjectMemory).mockResolvedValue(DECISIONS);
  });
  const open = () => show(<ProjectMemoryPage params={Promise.resolve({ slug: "billing-api" })} />);

  it("reads the shared memory by default, an agent's own one, a named one", async () => {
    await open();
    const picker = await screen.findByLabelText("Memory");
    await screen.findByRole("option", { name: "Decisions" });
    await waitFor(() => expect(api.memoryPending).toHaveBeenCalledWith("billing-api", {}));
    fireEvent.change(picker, { target: { value: "memory:decisions" } });
    await waitFor(() => expect(api.memoryPending).toHaveBeenLastCalledWith("billing-api", { memory: "decisions" }));
    fireEvent.change(picker, { target: { value: "own" } });
    expect(screen.getByText("Choose an agent to read its own memory.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Agent"), { target: { value: "architect" } });
    await waitFor(() => expect(api.memoryPending).toHaveBeenLastCalledWith("billing-api", { memory: "own", agent: "architect" }));
    // A decision is made in the memory being read.
    fireEvent.click(await screen.findByRole("button", { name: "Accept" }));
    await waitFor(() => expect(api.memoryDecide).toHaveBeenCalledWith("billing-api", "g1", "accept", { memory: "own", agent: "architect" }));
  });

  it("shows the organisation's memories that reach it, read-only", async () => {
    await open();
    fireEvent.change(await screen.findByLabelText("Memory"), { target: { value: "global:security-lessons" } });
    expect(await screen.findByRole("link", { name: "the organisation's registry" })).toHaveAttribute("href", "/memories/security-lessons");
    expect(screen.queryByRole("button", { name: "Accept" })).toBeNull();
    fireEvent.change(screen.getByLabelText("Search the memory"), { target: { value: "tls" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await waitFor(() => expect(api.searchGlobalMemory).toHaveBeenCalledWith("acme", "security-lessons", "tls"));
  });

  it("an owner creates a project memory; a developer cannot", async () => {
    session.me = { memberships: [{ org: "acme", role: "project_owner", project_slug: "billing-api" }] };
    await open();
    fireEvent.click(await screen.findByRole("button", { name: "New project memory" }));
    const drawer = await screen.findByRole("dialog");
    fireEvent.change(within(drawer).getByLabelText("Slug"), { target: { value: "runbooks" } });
    fireEvent.click(within(drawer).getByRole("button", { name: "Create" }));
    await waitFor(() =>
      expect(api.createProjectMemory).toHaveBeenCalledWith("billing-api", { slug: "runbooks", display_name: "runbooks", description: undefined }),
    );
  });

  it("offers a developer no creation", async () => {
    session.me = { memberships: [{ org: "acme", role: "developer", project_slug: "billing-api" }] };
    await open();
    await screen.findByLabelText("Memory");
    expect(screen.queryByRole("button", { name: "New project memory" })).toBeNull();
  });
});
