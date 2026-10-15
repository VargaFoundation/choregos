import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import NewAgentPage from "@/app/agents/new/page";
import { api } from "@/lib/api";

/**
 * A new agent on a full page (S39-05): a short first step, then the builder with its advanced
 * sections folded. The step is in the address, so the router is faked with a query that follows
 * `push`.
 */
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme", me: null }) }));
const nav = vi.hoisted(() => ({ query: new URLSearchParams(), push: vi.fn() }));
vi.mock("next/navigation", () => ({
  useSearchParams: () => nav.query,
  useRouter: () => ({
    push: (to: string) => {
      nav.push(to);
      nav.query = new URLSearchParams(to.split("?")[1] ?? "");
    },
    back: () => {
      nav.query = new URLSearchParams();
    },
  }),
}));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      createAgent: vi.fn().mockResolvedValue({ slug: "release-notes-writer", display_name: "Release notes writer", latest_version: 1 }),
      projects: vi.fn().mockResolvedValue({ items: [], meta: { has_more: false } }),
      platformModels: vi.fn().mockResolvedValue([]),
      backends: vi.fn().mockResolvedValue([]),
      orgConnectors: vi.fn().mockResolvedValue([]),
      skills: vi.fn().mockResolvedValue([]),
      knowledgeBases: vi.fn().mockResolvedValue([]),
    },
  };
});

const DESCRIPTION = "Writes the release notes of a project from its merged pull requests.";

function show() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <NewAgentPage />
    </QueryClientProvider>,
  );
}

/** Fills step 1 and goes to step 2, as a person does. */
async function toTheForm() {
  const view = show();
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Release notes writer" } });
  fireEvent.change(screen.getByLabelText("What should it do?"), { target: { value: DESCRIPTION } });
  fireEvent.click(screen.getByRole("button", { name: "Start from a blank form" }));
  view.rerender(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <NewAgentPage />
    </QueryClientProvider>,
  );
  return await screen.findByTestId("agent-builder");
}

describe("the new agent's page", () => {
  beforeEach(() => {
    nav.query = new URLSearchParams();
    nav.push.mockClear();
    vi.mocked(api.createAgent).mockClear();
  });

  it("asks for a name and what the agent should do, and nothing else, first", () => {
    show();
    const step = screen.getByTestId("step-describe");
    expect(within(step).getAllByRole("textbox")).toHaveLength(2);
    expect(within(step).getByLabelText("Name")).toBeInTheDocument();
    expect(within(step).getByLabelText("What should it do?")).toBeInTheDocument();
    expect(screen.queryByLabelText("Max turns")).toBeNull();
    expect(screen.getByRole("link", { name: "Or add one from the catalogue" })).toHaveAttribute("href", "/agents?catalogue=1");
    // Both fields are needed, the description with some substance.
    const start = screen.getByRole("button", { name: "Start from a blank form" });
    expect(start).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Release notes writer" } });
    fireEvent.change(screen.getByLabelText("What should it do?"), { target: { value: "too short" } });
    expect(start).toBeDisabled();
    fireEvent.change(screen.getByLabelText("What should it do?"), { target: { value: DESCRIPTION } });
    expect(start).toBeEnabled();
  });

  it("goes to the form on 'Start from a blank form', carrying the name, the slug and the description", async () => {
    const builder = await toTheForm();
    expect(nav.push).toHaveBeenCalledWith("/agents/new?step=configure");
    expect(within(builder).getByLabelText("Name")).toHaveValue("Release notes writer");
    expect(within(builder).getByLabelText("Slug")).toHaveValue("release-notes-writer");
    expect(within(builder).getByLabelText("Description")).toHaveValue(DESCRIPTION);
  });

  it("folds the seven advanced sections, each saying its value", async () => {
    const builder = await toTheForm();
    // Seven sections, and the help on template variables, are `<details>` left closed.
    expect(builder.querySelectorAll("details:not([open])")).toHaveLength(8);
    expect(builder.querySelectorAll("details[open]")).toHaveLength(0);
    const summary = (title: string) =>
      Array.from(builder.querySelectorAll("summary")).find((el) => el.textContent?.includes(title));
    expect(summary("Skills")).toHaveTextContent("Skillsnone");
    expect(summary("Eval suite")).toHaveTextContent("0 cases");
    expect(summary("Model and runtime")).toHaveTextContent("the project's profile · the project's backend");
  });

  it("'Save as draft' creates the agent as a draft, with its description", async () => {
    const builder = await toTheForm();
    fireEvent.click(within(builder).getByRole("button", { name: "Save as draft" }));
    await waitFor(() => expect(api.createAgent).toHaveBeenCalledTimes(1));
    expect(api.createAgent).toHaveBeenCalledWith(
      "acme",
      expect.objectContaining({ slug: "release-notes-writer", description: DESCRIPTION, lifecycle: "draft" }),
    );
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith("/agents/release-notes-writer"));
  });

  it("'Create and activate' creates it active", async () => {
    const builder = await toTheForm();
    fireEvent.click(within(builder).getByRole("button", { name: "Create and activate" }));
    await waitFor(() => expect(api.createAgent).toHaveBeenCalledTimes(1));
    expect(api.createAgent).toHaveBeenCalledWith("acme", expect.objectContaining({ lifecycle: "active", description: DESCRIPTION }));
  });

  it("keeps the trial and the costs in a side panel, one tab at a time", async () => {
    const builder = await toTheForm();
    const tabs = within(builder).getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["Try it", "What it costs and allows"]);
    expect(within(builder).getByTestId("banc-d-essai").closest("[role=tabpanel]")).not.toHaveAttribute("hidden");
    expect(within(builder).getByTestId("ce-que-ca-permet").closest("[role=tabpanel]")).toHaveAttribute("hidden");
    fireEvent.click(tabs[1]!);
    expect(within(builder).getByTestId("ce-que-ca-permet").closest("[role=tabpanel]")).not.toHaveAttribute("hidden");
  });
});
