import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Suspense, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AgentPage from "@/app/agents/[slug]/page";
import ProjectAgentsPage from "@/app/p/[slug]/agents/page";
import { creatableScopes, forkSlug, makeScopes, scopeLabel } from "@/components/agents/registre";
import { api } from "@/lib/api";
import type { Agent } from "@/lib/types";

/**
 * Agent governance in the console (S39-09b, ADR 0061). The console shows what the server decided
 * (`can`, `cannot_reason`) and never guesses from roles.
 */
const push = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
  usePathname: () => "/agents/developer",
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("next/dynamic", () => ({ default: () => () => null }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme", me: { email: "lea@acme.dev", memberships: [] } }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      agent: vi.fn(),
      agents: vi.fn(),
      agentMetrics: vi.fn().mockResolvedValue({ agent: "x", days: 30, runs: 0, succeeded: 0, failed: 0, cost_usd: 0 }),
      forkAgent: vi.fn(),
      changeAgentScope: vi.fn(),
      projects: vi.fn().mockResolvedValue({ items: [{ slug: "dev" }, { slug: "ops" }], meta: { has_more: false } }),
      projectAgents: vi.fn().mockResolvedValue([]),
      implicitAgents: vi.fn().mockResolvedValue([]),
    },
  };
});

const REASON = "This is a common agent: only an organisation admin changes it. Fork it into your project.";
const common = (over: Partial<Agent> = {}): Agent => ({
  slug: "developer",
  kind: "internal",
  display_name: "Developer",
  status: "active",
  latest_version: 3,
  lifecycle: "active",
  scope: "platform",
  project: null,
  can: { publish: false, manage: false, pin: true, fork_scopes: ["project", "personal"] },
  cannot_reason: REASON,
  versions: [{ version: 3, spec: {}, checksum: "sha256:abc" }],
  ...over,
});

async function open(node: ReactNode) {
  document.body.innerHTML = "";
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    render(
      <QueryClientProvider client={client}>
        <Suspense fallback="…">{node}</Suspense>
      </QueryClientProvider>,
    );
  });
}
const page = (tab?: string) => <AgentPage params={Promise.resolve({ slug: "developer" })} key={tab} />;

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState(null, "", "/agents/developer");
});

describe("the helpers", () => {
  it("names a scope, and proposes a fork slug", () => {
    expect(scopeLabel("platform")).toBe("Common");
    expect(scopeLabel("project", "dev")).toBe("Project · dev");
    expect(forkSlug("developer", "project", "dev", "lea@acme.dev")).toBe("developer-dev");
    expect(forkSlug("developer", "personal", null, "Lea.B@acme.dev")).toBe("developer-lea-b");
    expect(forkSlug("a".repeat(63), "project", "dev", null).length).toBeLessThanOrEqual(63);
  });

  it("offers a person the scopes their memberships can create; the API stays the judge", () => {
    const admin = creatableScopes([{ org: "acme", role: "org_admin" }], "acme");
    expect(makeScopes(admin, ["dev", "ops"])).toEqual(["org", "project", "personal"]);
    const owner = creatableScopes([{ org: "acme", role: "project_owner", project_slug: "dev" }], "acme");
    expect(makeScopes(owner, ["dev", "ops"])).toEqual(["project", "personal"]);
    expect(owner.projects).toEqual(["dev"]);
    const viewer = creatableScopes([{ org: "acme", role: "viewer" }], "acme");
    expect(makeScopes(viewer, ["dev"])).toEqual([]);
  });
});

describe("an agent the caller may not change", () => {
  it("disables the edit and says why in the page, with the server's sentence", async () => {
    vi.mocked(api.agent).mockResolvedValue(common());
    await open(page());
    expect((await screen.findByTestId("cannot-reason")).textContent).toContain(REASON);
    expect((await screen.findByTestId("scope")).textContent).toBe("Common");
    // The lifecycle controls are held back too.
    expect(screen.getByRole("button", { name: "Deprecate" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Suspend" })).toBeDisabled();
  });

  it("the Definition tab holds the publish button back with the reason, and no builder", async () => {
    window.history.replaceState(null, "", "/agents/developer?tab=definition");
    const search = vi.spyOn(URLSearchParams.prototype, "get").mockImplementation((k) => (k === "tab" ? "definition" : null));
    vi.mocked(api.agent).mockResolvedValue(common());
    await open(page());
    expect(await screen.findByRole("button", { name: "Publish v4" })).toBeDisabled();
    expect(screen.getByTestId("why-readonly").textContent).toBe(REASON);
    search.mockRestore();
  });

  it("nothing is held back when the server sends no `can` (an API without governance)", async () => {
    vi.mocked(api.agent).mockResolvedValue(common({ can: undefined, cannot_reason: null, scope: undefined }));
    await open(page());
    expect(await screen.findByRole("button", { name: "Deprecate" })).toBeEnabled();
    expect(screen.queryByTestId("cannot-reason")).toBeNull();
  });
});

describe("fork", () => {
  it("opens a dialog limited to the scopes the server allows, and posts to /fork with the choice", async () => {
    vi.mocked(api.agent).mockResolvedValue(common());
    vi.mocked(api.forkAgent).mockResolvedValue(common({ slug: "developer-dev", scope: "project", project: "dev" }));
    await open(page());
    fireEvent.click(await screen.findByRole("button", { name: "Fork" }));
    const scope = (await screen.findByLabelText("Scope")) as HTMLSelectElement;
    expect([...scope.options].map((o) => o.value)).toEqual(["project", "personal"]);
    await screen.findByRole("option", { name: "dev" });
    fireEvent.change(screen.getByLabelText("Source version"), { target: { value: "2" } });
    expect((screen.getByLabelText("New slug") as HTMLInputElement).value).toBe("developer-dev");
    fireEvent.submit(screen.getByRole("form", { name: "Fork" }));
    await waitFor(() =>
      expect(api.forkAgent).toHaveBeenCalledWith("acme", "developer", {
        slug: "developer-dev",
        display_name: null,
        scope: "project",
        project: "dev",
        version: 2,
      }),
    );
    await waitFor(() => expect(push).toHaveBeenCalledWith("/agents/developer-dev"));
  });

  it("no Fork button when the server offers no scope to fork into", async () => {
    vi.mocked(api.agent).mockResolvedValue(common({ can: { publish: false, manage: false, fork_scopes: [] } }));
    await open(page());
    await screen.findByTestId("cannot-reason");
    expect(screen.queryByRole("button", { name: "Fork" })).toBeNull();
  });

  it("a refusal of the API is shown in the dialog", async () => {
    vi.mocked(api.agent).mockResolvedValue(common());
    vi.mocked(api.forkAgent).mockRejectedValue(new Error("`developer-lea` already exists"));
    await open(page());
    fireEvent.click(await screen.findByRole("button", { name: "Fork" }));
    fireEvent.change(await screen.findByLabelText("Scope"), { target: { value: "personal" } });
    fireEvent.submit(screen.getByRole("form", { name: "Fork" }));
    expect(await screen.findByText("`developer-lea` already exists")).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });
});

describe("the upstream of a fork", () => {
  const fork = (up: Agent["forked_from"]) =>
    common({ slug: "developer", scope: "project", project: "dev", forked_from: up, can: { publish: true, manage: true, fork_scopes: ["personal"] }, cannot_reason: null });

  it("says when the source moved on, and never moves by itself", async () => {
    vi.mocked(api.agent).mockResolvedValue(fork({ slug: "base", version: 1, scope: "platform", latest_version: 3, behind: true, available: true }));
    await open(page());
    const banner = await screen.findByTestId("upstream");
    expect(banner.textContent).toContain("v1");
    expect(banner.textContent).toContain("moved on to v3");
    expect(screen.getByRole("link", { name: "base" })).toHaveAttribute("href", "/agents/base");
  });

  it("says it is up to date, and when the source is gone", async () => {
    vi.mocked(api.agent).mockResolvedValue(fork({ slug: "base", version: 3, latest_version: 3, behind: false, available: true }));
    await open(page());
    expect((await screen.findByTestId("upstream")).textContent).toContain("up to date");
    vi.mocked(api.agent).mockResolvedValue(fork({ slug: "base", version: 3, available: false }));
    await open(page());
    expect((await screen.findByTestId("upstream")).textContent).toContain("deleted or not visible");
  });
});

describe("change scope", () => {
  const personal = (can: Agent["can"]) =>
    common({ slug: "developer", scope: "personal", can, cannot_reason: null });

  it("a manager promotes a personal agent into a project, through the API", async () => {
    vi.mocked(api.agent).mockResolvedValue(personal({ publish: true, manage: true, fork_scopes: [] }));
    vi.mocked(api.changeAgentScope).mockResolvedValue(common({ scope: "project", project: "dev" }));
    await open(page());
    const panel = await screen.findByTestId("scope-panel");
    expect(panel.textContent).toContain("never runs in a project");
    fireEvent.change(screen.getByLabelText("Move to"), { target: { value: "project" } });
    await screen.findByRole("option", { name: "ops" });
    fireEvent.click(screen.getByRole("button", { name: "Change scope" }));
    await waitFor(() => expect(api.changeAgentScope).toHaveBeenCalledWith("acme", "developer", { scope: "project", project: "dev" }));
  });

  it("is not offered without `can.manage`, nor on a common agent", async () => {
    vi.mocked(api.agent).mockResolvedValue(personal({ publish: true, manage: false, fork_scopes: [] }));
    await open(page());
    await screen.findByTestId("scope");
    expect(screen.queryByTestId("scope-panel")).toBeNull();
    vi.mocked(api.agent).mockResolvedValue(common({ can: { publish: true, manage: true, fork_scopes: [] } }));
    await open(page());
    await screen.findByTestId("scope");
    expect(screen.queryByTestId("scope-panel")).toBeNull();
  });

  it("an admin editing a common agent is warned that it diverges from the catalogue", async () => {
    const search = vi.spyOn(URLSearchParams.prototype, "get").mockImplementation((k) => (k === "tab" ? "definition" : null));
    vi.mocked(api.agent).mockResolvedValue(common({ can: { publish: true, manage: true, fork_scopes: ["org"] }, cannot_reason: null }));
    await open(page());
    expect((await screen.findByTestId("diverges")).textContent).toContain("Prefer a fork");
    search.mockRestore();
  });
});

describe("a project's agents", () => {
  const mine = common({ slug: "dev-agent", display_name: "Dev agent", scope: "project", project: "dev" });
  const other = common({ slug: "ops-agent", display_name: "Ops agent", scope: "project", project: "ops" });
  const personal = common({ slug: "scratch", display_name: "Scratch", scope: "personal" });
  const org = common({ slug: "shared", display_name: "Shared", scope: "org" });

  it("lists the project's own agents first, and offers neither a personal agent nor another project's to pin", async () => {
    vi.mocked(api.agents).mockResolvedValue([mine, other, personal, org]);
    await open(<ProjectAgentsPage params={Promise.resolve({ slug: "dev" })} />);
    const owned = await screen.findByTestId("project-owned");
    expect(owned.textContent).toContain("Dev agent");
    expect(owned.textContent).not.toContain("Ops agent");
    const choices = [...(screen.getByLabelText("Agent") as HTMLSelectElement).options].map((o) => o.textContent);
    expect(choices).toContain("Dev agent");
    expect(choices).toContain("Shared");
    expect(choices).not.toContain("Scratch");
    expect(choices).not.toContain("Ops agent");
    expect(screen.getByRole("link", { name: "New project agent" })).toHaveAttribute("href", "/agents/new?scope=project&project=dev");
  });
});
