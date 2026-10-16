import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AgentsPage from "@/app/agents/page";
import { AgentDetailsCard } from "@/components/agents/details";
import { MAX_TAGS, normalizeTag, TagEditor, TagFilter, tagProblem, withTags } from "@/components/agents/tags";
import { api } from "@/lib/api";
import type { Agent } from "@/lib/types";

/** Agent tags (S40-10): labels outside the versions, edited by whoever may publish, filtered with `?tag=`. */
const query = vi.hoisted(() => ({ current: new URLSearchParams() }));
const replace = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({
  useSearchParams: () => query.current,
  useRouter: () => ({ push: vi.fn(), replace }),
}));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme", me: null }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      agents: vi.fn(),
      agentMetricsList: vi.fn().mockResolvedValue([]),
      agentCatalogue: vi.fn().mockResolvedValue([]),
      updateAgent: vi.fn(),
    },
  };
});

const agent = (slug: string, tags: string[], over: Partial<Agent> = {}): Agent =>
  ({ slug, kind: "internal", display_name: slug, status: "active", latest_version: 1, tags, ...over }) as Agent;

function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  query.current = new URLSearchParams();
  vi.mocked(api.agents).mockResolvedValue([
    agent("architect", ["madr", "architecture", "design"]),
    agent("ux-designer", ["ux", "design"]),
    agent("triager", []),
  ]);
});

describe("tag helpers", () => {
  it("normalises what is typed into a lowercase slug", () => {
    expect(normalizeTag(" Design System ")).toBe("design-system");
    expect(normalizeTag("Archi_tecture!")).toBe("archi-tecture");
    expect(normalizeTag("\u00c9thique")).toBe("ethique");
    expect(normalizeTag("x".repeat(40))).toHaveLength(32);
    expect(normalizeTag("---")).toBe("");
  });

  it("says why a tag is refused", () => {
    expect(tagProblem("madr", ["madr"])).toMatch(/already there/);
    expect(tagProblem("", [])).toMatch(/at least one/);
    const ten = Array.from({ length: MAX_TAGS }, (_, n) => `t${n}`);
    expect(tagProblem("one-more", ten)).toMatch(/at most 10/);
    expect(tagProblem("fine", ["madr"])).toBeNull();
  });

  it("keeps the agents that carry every selected tag", () => {
    const all = [agent("a", ["x", "y"]), agent("b", ["x"]), agent("c", [])];
    expect(withTags(all, []).map((a) => a.slug)).toEqual(["a", "b", "c"]);
    expect(withTags(all, ["x"]).map((a) => a.slug)).toEqual(["a", "b"]);
    expect(withTags(all, ["x", "y"]).map((a) => a.slug)).toEqual(["a"]);
  });
});

function Editor({ start = [] as string[] }) {
  const [tags, setTags] = useState(start);
  return <TagEditor value={tags} onChange={setTags} suggestions={["madr"]} />;
}

describe("the tag editor", () => {
  it("adds a normalised tag on Enter and on a comma, and removes it with its button", () => {
    render(<Editor />);
    const input = screen.getByLabelText("Tags");
    fireEvent.change(input, { target: { value: "Design System" } });
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.change(input, { target: { value: "madr," } });
    expect(screen.getByText("design-system")).toBeTruthy();
    expect(screen.getByText("madr")).toBeTruthy();
    expect(screen.getByText(/2 of 10/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Remove madr" }));
    expect(screen.queryByText("madr")).toBeNull();
  });

  it("removes the last tag with Backspace on an empty input", () => {
    render(<Editor start={["a", "b"]} />);
    fireEvent.keyDown(screen.getByLabelText("Tags"), { key: "Backspace" });
    expect(screen.queryByRole("button", { name: "Remove b" })).toBeNull();
    expect(screen.getByRole("button", { name: "Remove a" })).toBeTruthy();
  });

  it("refuses an eleventh tag with the sentence, and a duplicate", () => {
    render(<Editor start={Array.from({ length: MAX_TAGS }, (_, n) => `t${n}`)} />);
    const input = screen.getByLabelText("Tags");
    fireEvent.change(input, { target: { value: "eleven" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(screen.getByRole("alert").textContent).toMatch(/at most 10 tags/);
    expect(screen.queryByText("eleven")).toBeNull();
    fireEvent.change(input, { target: { value: "t1" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(screen.getByRole("alert").textContent).toMatch(/already there/);
  });
});

describe("the tag filter", () => {
  it("lists every tag with its count and toggles it", () => {
    const onChange = vi.fn();
    render(<TagFilter agents={[agent("a", ["x", "y"]), agent("b", ["x"])]} value={["x"]} onChange={onChange} />);
    expect(screen.getByRole("button", { name: "x (2)" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "y (1)" }));
    expect(onChange).toHaveBeenCalledWith(["x", "y"]);
    fireEvent.click(screen.getByRole("button", { name: "x (2)" }));
    expect(onChange).toHaveBeenLastCalledWith([]);
  });
});

describe("the registry", () => {
  it("shows a row's tags and lists everyone without a filter", async () => {
    await act(async () => void wrap(<AgentsPage />));
    const row = await screen.findByTestId("agent-architect");
    expect(within(row).getByText("madr")).toBeTruthy();
    expect(screen.getByTestId("agent-triager")).toBeTruthy();
  });

  it("preselects the tags of the address and keeps the agents that carry them all", async () => {
    query.current = new URLSearchParams("tag=design&tag=madr");
    await act(async () => void wrap(<AgentsPage />));
    await screen.findByTestId("agent-architect");
    expect(screen.queryByTestId("agent-ux-designer")).toBeNull();
    expect(screen.queryByTestId("agent-triager")).toBeNull();
    const filter = screen.getByTestId("tag-filter");
    expect(within(filter).getByRole("button", { name: "madr (1)" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("writes the selection in the address", async () => {
    await act(async () => void wrap(<AgentsPage />));
    await screen.findByTestId("agent-architect");
    fireEvent.click(within(screen.getByTestId("tag-filter")).getByRole("button", { name: "madr (1)" }));
    expect(replace).toHaveBeenCalledWith("/agents?tag=madr");
  });

  it("says when nothing carries the tags, and clears them", async () => {
    query.current = new URLSearchParams("tag=madr&tag=ux");
    await act(async () => void wrap(<AgentsPage />));
    expect(await screen.findByText("No agent tagged madr + ux")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Clear the tags" }));
    expect(replace).toHaveBeenCalledWith("/agents");
  });
});

describe("the details card", () => {
  const publishable = agent("architect", ["madr"], { description: "Writes ADRs.", can: { publish: true, manage: true, pin: true, fork_scopes: [] } });

  it("saves the name, description and tags in one PATCH", async () => {
    vi.mocked(api.updateAgent).mockResolvedValue(publishable);
    await act(async () => void wrap(<AgentDetailsCard org="acme" agent={publishable} />));
    fireEvent.click(screen.getByRole("button", { name: "Edit details" }));
    const input = screen.getByLabelText("Tags");
    fireEvent.change(input, { target: { value: "adr" } });
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.click(screen.getByRole("button", { name: "Save details" }));
    await waitFor(() => expect(api.updateAgent).toHaveBeenCalled());
    expect(api.updateAgent).toHaveBeenCalledWith("acme", "architect", {
      display_name: "architect",
      description: "Writes ADRs.",
      tags: ["madr", "adr"],
    });
  });

  it("is read only when the caller may not publish: no button, the tags still shown", async () => {
    const locked = agent("developer", ["software"], {
      can: { publish: false, manage: false, pin: true, fork_scopes: [] },
    });
    await act(async () => void wrap(<AgentDetailsCard org="acme" agent={locked} />));
    expect(screen.queryByRole("button", { name: "Edit details" })).toBeNull();
    expect(screen.getByText("software")).toBeTruthy();
  });
});
