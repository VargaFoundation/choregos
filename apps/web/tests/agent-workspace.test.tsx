import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AgentBuilder, cleanSpec, openByDefault, sectionSummaries } from "@/components/agents/builder";
import { diffSpecs } from "@/lib/agent-diff";
import { api } from "@/lib/api";
import { effectiveWorkspace, workspaceLine } from "@/lib/workspace";

/** An agent declares the workspace it needs (S51-03, ADR 0119): none, read or write. */

vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme", me: null }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      createAgent: vi.fn().mockResolvedValue({ slug: "analyst", display_name: "Analyst", latest_version: 1 }),
      projects: vi.fn().mockResolvedValue({ items: [], meta: { has_more: false } }),
      platformModels: vi.fn().mockResolvedValue([]),
      backends: vi.fn().mockResolvedValue([]),
      orgConnectors: vi.fn().mockResolvedValue([]),
      skills: vi.fn().mockResolvedValue([]),
      knowledgeBases: vi.fn().mockResolvedValue([]),
      policyCeiling: vi.fn().mockResolvedValue({ ceiling: {} }),
    },
  };
});

const HOSTED = { kind: "hosted" as const, source: { type: "image" as const, ref: "ghcr.io/acme/t:1" } };

describe("what a version's workspace means", () => {
  it("is write when unsaid, none for a remote agent, the runtime's for a hosted one", () => {
    expect(effectiveWorkspace({})).toBe("write");
    expect(effectiveWorkspace({ workspace: "read" })).toBe("read");
    expect(effectiveWorkspace({ runtime: { kind: "a2a", card_url: "https://rovo.example/card" } })).toBe("none");
    expect(effectiveWorkspace({ runtime: { ...HOSTED, workspace: "write" } })).toBe("write");
    expect(workspaceLine({})).toBe("write — edits the project's repository (default)");
    expect(workspaceLine({ workspace: "none" })).toBe("none — never sees the repository");
  });

  it("says it on the folded section, and opens it only when declared", () => {
    expect(sectionSummaries({}).workspace).toBe("write (default)");
    expect(sectionSummaries({ workspace: "read" }).workspace).toBe("read");
    expect(openByDefault({}).workspace).toBe(false);
    expect(openByDefault({ workspace: "none" }).workspace).toBe(true);
  });

  it("is left out of a version that does not say it, so its digest does not move", () => {
    expect(cleanSpec({})).not.toHaveProperty("workspace", expect.anything());
    expect(cleanSpec({ workspace: null }).workspace).toBeUndefined();
    expect(cleanSpec({ workspace: "read" }).workspace).toBe("read");
  });

  it("is a change between two versions, and unsaid reads as write", () => {
    expect(diffSpecs({}, { workspace: "write" })).toEqual([]);
    const [change] = diffSpecs({}, { workspace: "none" });
    expect(change).toMatchObject({
      group: "capabilities",
      field: "workspace",
      kind: "changed",
      before: "write — edits the project's repository",
      after: "none — never sees the repository",
    });
  });
});

describe("the builder's Workspace section", () => {
  beforeEach(() => vi.mocked(api.createAgent).mockClear());

  function open() {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AgentBuilder org="acme" mode={{ kind: "create" }} />
      </QueryClientProvider>,
    );
    return screen.getByTestId("agent-builder");
  }

  async function publish(builder: HTMLElement) {
    fireEvent.change(within(builder).getByLabelText("Slug"), { target: { value: "analyst" } });
    fireEvent.click(within(builder).getByRole("button", { name: "Create and activate" }));
    await waitFor(() => expect(api.createAgent).toHaveBeenCalled());
    return vi.mocked(api.createAgent).mock.calls[0]![1].spec!;
  }

  it("by default writes nothing: a run edits the project's repository as before", async () => {
    const builder = open();
    expect(within(builder).getByLabelText("The default: write — edits the project's repository")).toBeChecked();
    expect((await publish(builder)).workspace).toBeUndefined();
  });

  it.each([
    ["none — never sees the repository", "none"],
    ["read — reads the project's repository, writes none of it", "read"],
  ])("“%s” writes `%s`, and the folded section says it", async (label, value) => {
    const builder = open();
    fireEvent.click(within(builder).getByLabelText(label));
    expect(within(builder).getByText("Workspace").closest("summary")).toHaveTextContent(value);
    expect(within(builder).getByText(/The repository comes from the project/)).toBeInTheDocument();
    expect((await publish(builder)).workspace).toBe(value);
  });
});
