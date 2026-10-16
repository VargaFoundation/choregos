import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Suspense, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AgentPage from "@/app/agents/[slug]/page";
import { AgentBuilder } from "@/components/agents/builder";
import { ApiError, api } from "@/lib/api";
import type { Agent } from "@/lib/types";

/**
 * An agent's Definition and History (S40-12, ADR 0076): one version read-only until Edit, a message to
 * publish, the stale-base answer, a history that compares any two revisions and restores as a new one.
 */
const query = vi.hoisted(() => ({ current: new URLSearchParams() }));
const nav = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => nav,
  usePathname: () => "/agents/architect",
  useSearchParams: () => query.current,
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
      agents: vi.fn().mockResolvedValue([]),
      agentMetrics: vi.fn().mockResolvedValue({ agent: "x", days: 30, runs: 0, succeeded: 0, failed: 0, cost_usd: 0 }),
      publishAgentVersionWithMessage: vi.fn(),
      restoreAgentVersion: vi.fn(),
      updateAgent: vi.fn(),
      platformModels: vi.fn().mockResolvedValue([]),
      backends: vi.fn().mockResolvedValue([]),
      orgConnectors: vi.fn().mockResolvedValue([]),
      skills: vi.fn().mockResolvedValue([]),
      knowledgeBases: vi.fn().mockResolvedValue([]),
    },
  };
});

const SPEC_3 = {
  instructions: Array.from({ length: 20 }, (_, n) => `Instruction line ${n + 1}`).join("\n"),
  model: "profile:standard",
  budget: { run_usd: 2 },
  skills: [{ slug: "madr-4", version: 1 }],
  knowledge: [{ slug: "adr-framework" }],
  memory: "agent" as const,
  evals: [{ name: "has a title", must_contain: ["#"] }],
};

const architect = (over: Partial<Agent> = {}): Agent => ({
  slug: "architect",
  kind: "internal",
  display_name: "Architect",
  description: "Writes ADRs.",
  tags: ["madr"],
  status: "active",
  lifecycle: "active",
  latest_version: 3,
  scope: "org",
  can: { publish: true, manage: true, pin: true, fork_scopes: [] },
  versions: [
    {
      version: 3,
      spec: SPEC_3,
      checksum: "sha256:3",
      created_by: "augustin@acme.dev",
      created_at: new Date().toISOString(),
      message: "Tighten the run budget",
      pinned_by: [],
    },
    {
      version: 2,
      spec: { ...SPEC_3, instructions: "Write the ADR." },
      checksum: "sha256:2",
      created_by: "lea@acme.dev",
      message: "Restore v1",
      restored_from: 1,
      pinned_by: [],
    },
    { version: 1, spec: { instructions: "Write the ADR." }, checksum: "sha256:1", created_by: "lea@acme.dev", pinned_by: ["billing-api"] },
  ],
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
const page = () => <AgentPage params={Promise.resolve({ slug: "architect" })} />;

beforeEach(() => {
  vi.clearAllMocks();
  query.current = new URLSearchParams("tab=definition");
  vi.mocked(api.agent).mockResolvedValue(architect());
});

describe("the Definition tab", () => {
  it("shows one version, read-only: no field to type in before Edit", async () => {
    await open(page());
    await screen.findByTestId("version-bar");
    expect(screen.queryAllByRole("textbox")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: /^Publish v/ })).toBeNull();
    expect(screen.getByRole("link", { name: "Edit" }).getAttribute("href")).toBe("/agents/architect?tab=definition&edit=1");
  });

  it("says which version is current, its message and who pins what", async () => {
    await open(page());
    const bar = await screen.findByTestId("version-bar");
    expect(within(bar).getByText("Current")).toBeTruthy();
    expect(within(bar).getByTestId("version-message").textContent).toBe("“Tighten the run budget”");
    expect(within(bar).getByTestId("pinned").textContent).toBe("Pinned by billing-api (v1)");
    expect(within(bar).getByText(/what a run loads unless a project pins another/)).toBeTruthy();
  });

  it("shows every group with its summary, and what the old page left out", async () => {
    await open(page());
    const groups = await screen.findByTestId("definition-groups");
    for (const title of ["About", "Instructions", "Model and runtime", "Capabilities", "Limits and budget", "Eval suite"])
      expect(within(groups).getByText(title)).toBeTruthy();
    expect(within(groups).getByText("20 lines")).toBeTruthy();
    expect(within(groups).getByText("1 case")).toBeTruthy();
    expect(groups.textContent).toContain("adr-framework");
    expect(groups.textContent).toContain("Its own memory in the project");
    expect(groups.textContent).toContain("has a title");
  });

  it("lists the memories a version binds, in order, and where it writes (S40-11 step 18)", async () => {
    const spec = {
      ...SPEC_3,
      memory: undefined,
      memories: [
        { scope: "global" as const, slug: "security-lessons", mode: "read" as const },
        { scope: "project" as const, slug: "shared", mode: "write" as const },
      ],
    };
    vi.mocked(api.agent).mockResolvedValue(architect({ versions: [{ version: 3, spec, checksum: "sha256:3", pinned_by: [] }] }));
    await open(page());
    const list = await screen.findByTestId("memory-bindings");
    const items = within(list).getAllByRole("listitem").map((n) => n.textContent ?? "");
    expect(items[0]).toContain("security-lessons (organisation)");
    expect(items[0]).toContain("read");
    expect(items[1]).toContain("The project's shared memory");
    expect(items[1]).toContain("read and written");
    expect(list.textContent).toContain("writes to the project's shared memory");
    // The folded group says the same in one line.
    expect(screen.getByText(/1 organisation, 1 project · writes to the project's shared memory/)).toBeTruthy();
  });

  it("folds long instructions behind Show all", async () => {
    await open(page());
    const text = (await screen.findByLabelText("Instructions")).textContent ?? "";
    expect(text).toContain("Instruction line 12");
    expect(text).not.toContain("Instruction line 13");
    fireEvent.click(screen.getByRole("button", { name: "Show all 20 lines" }));
    expect(screen.getByLabelText("Instructions").textContent).toContain("Instruction line 20");
  });

  it("reading an older version says so, offers the comparison and the restore", async () => {
    query.current = new URLSearchParams("tab=definition&version=1");
    await open(page());
    expect(await screen.findByText("You are reading v1")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Compare with v3" }).getAttribute("href")).toBe("/agents/architect?tab=history&compare=1..3");
    fireEvent.click(screen.getByRole("button", { name: "Restore as v4" }));
    expect(await screen.findByRole("form", { name: "Restore" })).toBeTruthy();
  });

  it("holds Edit back with the server's reason when the caller may not change the agent", async () => {
    vi.mocked(api.agent).mockResolvedValue(
      architect({ can: { publish: false, manage: false, fork_scopes: [] }, cannot_reason: "Only an organisation admin changes this." }),
    );
    await open(page());
    expect(await screen.findByRole("button", { name: "Edit" })).toBeDisabled();
    expect(screen.getByTestId("why-readonly").textContent).toBe("Only an organisation admin changes this.");
  });
});

describe("the editor", () => {
  const editor = (start = SPEC_3) =>
    open(<AgentBuilder org="acme" mode={{ kind: "version", agent: architect() }} start={start} onCancel={vi.fn()} />);

  it("keeps Publish disabled until a message is typed, then sends it with the base version", async () => {
    vi.mocked(api.publishAgentVersionWithMessage).mockResolvedValue({ version: 4, spec: SPEC_3, checksum: "sha256:4" });
    await editor();
    const publish = screen.getByRole("button", { name: "Publish v4" });
    expect(publish).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Instructions"), { target: { value: "Changed." } });
    fireEvent.change(screen.getByLabelText("What changed and why"), { target: { value: "  Shorter instructions " } });
    expect(publish).toBeEnabled();
    fireEvent.click(publish);
    await waitFor(() => expect(api.publishAgentVersionWithMessage).toHaveBeenCalled());
    const [org, slug, body] = vi.mocked(api.publishAgentVersionWithMessage).mock.calls[0]!;
    expect([org, slug, body.message, body.base_version, body.spec.instructions]).toEqual(["acme", "architect", "Shorter instructions", 3, "Changed."]);
  });

  it("counts the changes live in a Changes tab, against the version it started from", async () => {
    await editor();
    expect(screen.getByRole("tab", { name: "Changes (0)" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Instructions"), { target: { value: `${SPEC_3.instructions}\nOne more.` } });
    const tab = screen.getByRole("tab", { name: "Changes (1)" });
    fireEvent.click(tab);
    expect(within(screen.getByRole("tabpanel", { name: "Changes (1)" })).getByTestId("change-instructions")).toBeTruthy();
  });

  it("answers a stale base with a way forward, keeps the form, and publishes on top when asked", async () => {
    const stale = new ApiError(409, { detail: "x", code: "stale_base", latest_version: 4, latest_author: "bob@acme.dev" });
    vi.mocked(api.publishAgentVersionWithMessage)
      .mockRejectedValueOnce(stale)
      .mockResolvedValueOnce({ version: 5, spec: SPEC_3, checksum: "sha256:5" });
    await editor();
    fireEvent.change(screen.getByRole("textbox", { name: "Instructions" }), { target: { value: "Mine." } });
    fireEvent.change(screen.getByLabelText("What changed and why"), { target: { value: "My change" } });
    fireEvent.click(screen.getByRole("button", { name: "Publish v4" }));
    const note = await screen.findByTestId("stale-base");
    expect(note.textContent).toContain("v4 was published by bob@acme.dev since you started from v3.");
    expect(within(note).getByRole("link", { name: /Compare v3 → v4/ }).getAttribute("href")).toBe("/agents/architect?tab=history&compare=3..4");
    expect((screen.getByRole("textbox", { name: "Instructions" }) as HTMLTextAreaElement).value).toBe("Mine.");
    fireEvent.click(within(note).getByRole("button", { name: "Publish on top of v4" }));
    await waitFor(() => expect(api.publishAgentVersionWithMessage).toHaveBeenCalledTimes(2));
    expect(vi.mocked(api.publishAgentVersionWithMessage).mock.calls[1]![2].base_version).toBe(4);
  });

  it("says nothing changed when the server answers no_change", async () => {
    vi.mocked(api.publishAgentVersionWithMessage).mockRejectedValue(
      new ApiError(409, { detail: "x", code: "no_change", latest_version: 3 }),
    );
    await editor();
    fireEvent.change(screen.getByLabelText("What changed and why"), { target: { value: "Same" } });
    fireEvent.click(screen.getByRole("button", { name: "Publish v4" }));
    await waitFor(() => expect(api.publishAgentVersionWithMessage).toHaveBeenCalled());
    expect(await screen.findByText("Nothing changed since v3.")).toBeTruthy();
  });

  it("asks before discarding changes", async () => {
    const onCancel = vi.fn();
    await open(<AgentBuilder org="acme" mode={{ kind: "version", agent: architect() }} start={SPEC_3} onCancel={onCancel} />);
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(onCancel).toHaveBeenCalledTimes(1); // nothing changed: no question
    fireEvent.change(screen.getByLabelText("Instructions"), { target: { value: "Changed." } });
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(screen.getByText("Discard 1 change?")).toBeTruthy();
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});

describe("the History tab", () => {
  it("lists the revisions with messages, restore origin and pins", async () => {
    query.current = new URLSearchParams("tab=history");
    await open(page());
    const list = await screen.findByTestId("history");
    expect(within(screen.getByTestId("revision-3")).getByText("Current")).toBeTruthy();
    expect(screen.getByTestId("revision-3").textContent).toContain("Tighten the run budget");
    expect(screen.getByTestId("revision-2").textContent).toContain("Restored from v1");
    expect(screen.getByTestId("revision-1").textContent).toContain("Pinned by billing-api");
    expect(screen.getByTestId("revision-1").textContent).toContain("No message");
    expect(list.querySelectorAll("li")).toHaveLength(3);
    expect(screen.queryByRole("button", { name: "Restore v3" })).toBeNull();
  });

  it("opens on ?tab=versions, the former name", async () => {
    query.current = new URLSearchParams("tab=versions");
    await open(page());
    expect(await screen.findByTestId("history")).toBeTruthy();
  });

  it("compares two ticked revisions", async () => {
    query.current = new URLSearchParams("tab=history");
    await open(page());
    await screen.findByTestId("history");
    fireEvent.click(within(screen.getByTestId("revision-1")).getByRole("checkbox"));
    fireEvent.click(within(screen.getByTestId("revision-3")).getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Compare v1 and v3" }));
    expect(nav.push).toHaveBeenCalledWith("/agents/architect?tab=history&compare=1..3");
  });

  it("shows the structured difference of ?compare=1..3", async () => {
    query.current = new URLSearchParams("tab=history&compare=1..3");
    await open(page());
    const compare = await screen.findByTestId("compare");
    expect(within(compare).getByTestId("change-instructions")).toBeTruthy();
    expect(within(compare).getByTestId("change-budget.run_usd").textContent).toContain("Added");
    // `memory: "agent"` is read as the bindings it means: the shared memory goes, the agent's own comes (S40-11).
    const memories = within(compare).getAllByTestId("change-memories").map((n) => n.textContent ?? "");
    expect(memories.some((m) => m.includes("Removed"))).toBe(true);
    expect(memories.some((m) => m.includes("Added") && m.includes("Its own memory in the project"))).toBe(true);
    expect(compare.textContent).toContain("Knowledge base adr-framework");
  });

  it("restores a revision as a new one, from the current latest", async () => {
    vi.mocked(api.restoreAgentVersion).mockResolvedValue({ version: 4, spec: SPEC_3, checksum: "sha256:1" });
    query.current = new URLSearchParams("tab=history");
    await open(page());
    fireEvent.click(await screen.findByRole("button", { name: "Restore v1" }));
    expect(await screen.findByText(/This copies v1 as a new revision, v4/)).toBeTruthy();
    fireEvent.submit(screen.getByRole("form", { name: "Restore" }));
    await waitFor(() => expect(api.restoreAgentVersion).toHaveBeenCalledWith("acme", "architect", 1, { message: null, base_version: 3 }));
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith("/agents/architect?tab=history"));
  });

  it("shows the refusal of a restore in words", async () => {
    vi.mocked(api.restoreAgentVersion).mockRejectedValue(new ApiError(409, { detail: "x", code: "stale_base", latest_version: 4, latest_author: "bob@acme.dev" }));
    query.current = new URLSearchParams("tab=history");
    await open(page());
    fireEvent.click(await screen.findByRole("button", { name: "Restore v2" }));
    fireEvent.submit(await screen.findByRole("form", { name: "Restore" }));
    expect(await screen.findByText("v4 was published by bob@acme.dev since you started.")).toBeTruthy();
  });

  it("disables Restore for someone who may not change the agent", async () => {
    vi.mocked(api.agent).mockResolvedValue(architect({ can: { publish: false, manage: false, fork_scopes: [] }, cannot_reason: "Not yours." }));
    query.current = new URLSearchParams("tab=history");
    await open(page());
    expect(await screen.findByRole("button", { name: "Restore v1" })).toBeDisabled();
    expect(screen.getAllByText("Not yours.", { exact: false }).length).toBeGreaterThan(0);
  });
});
