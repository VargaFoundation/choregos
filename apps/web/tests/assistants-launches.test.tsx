import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ConversationView } from "@/components/assistants/conversation-view";
import { InstallationLaunches } from "@/components/assistants/installation-launches";
import { api } from "@/lib/api";
import type { Assistant } from "@/lib/assistants-api";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), usePathname: () => "/assistants" }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "varga", me: { memberships: [] } }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, projects: vi.fn(), workflows: vi.fn() } };
});

const mocks = vi.hoisted(() => ({
  bindLaunch: vi.fn(),
  conversation: vi.fn(),
  conversationActions: vi.fn(),
  confirmAction: vi.fn(),
  refuseAction: vi.fn(),
  memory: vi.fn(),
  myAssistants: vi.fn(),
  streamTurn: vi.fn(),
}));
vi.mock("@/lib/assistants-api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/assistants-api")>();
  return { ...real, assistantsApi: { ...real.assistantsApi, ...mocks }, streamTurn: mocks.streamTurn };
});

function wrap(ui: React.ReactNode) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui}</QueryClientProvider>,
  );
}

const launch = {
  name: "portfolio_review",
  description: "Ask the review team for a portfolio review document.",
  required: true,
  input_schema: { type: "object" },
  project: null,
  workflow: null,
  enabled: false,
  bound_by: null,
};

const installed = (launches: unknown[]) => ({ slug: "invest", launches }) as unknown as Assistant;

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.projects).mockResolvedValue({
    items: [{ id: "p1", slug: "reviews", name: "Reviews" }],
    meta: { has_more: false },
  } as never);
  vi.mocked(api.workflows).mockResolvedValue([{ name: "review", version: 1, is_default: false, open_items: 0 }] as never);
  mocks.bindLaunch.mockResolvedValue({});
  mocks.myAssistants.mockResolvedValue({ items: [] });
});

describe("the launches of an installation", () => {
  it("binds a launch to a project's workflow", async () => {
    wrap(<InstallationLaunches org="varga" slug="invest" assistant={installed([launch])} />);
    const row = await screen.findByRole("group", { name: "Launch portfolio_review" });
    expect(within(row).getByText("Not bound")).toBeInTheDocument();
    const bind = within(row).getByRole("button", { name: "Bind" });
    expect(bind).toBeDisabled();
    await waitFor(() => expect(within(row).getByRole("option", { name: "Reviews" })).toBeInTheDocument());
    fireEvent.change(within(row).getByLabelText("Project for portfolio_review"), { target: { value: "reviews" } });
    await waitFor(() => expect(within(row).getByRole("option", { name: "review" })).toBeInTheDocument());
    expect(api.workflows).toHaveBeenCalledWith("p1");
    fireEvent.change(within(row).getByLabelText("Workflow for portfolio_review"), { target: { value: "review" } });
    fireEvent.click(bind);
    await waitFor(() =>
      expect(mocks.bindLaunch).toHaveBeenCalledWith("varga", "invest", "portfolio_review", {
        project: "reviews",
        workflow: "review",
        enabled: true,
      }),
    );
  });

  it("says where a launch is bound, shows a refusal, and switches an optional one off", async () => {
    mocks.bindLaunch.mockRejectedValueOnce(new Error("binding a launch to `reviews` needs the right to create work items there"));
    const bound = { ...launch, required: false, project: "reviews", workflow: "review", enabled: true };
    wrap(<InstallationLaunches org="varga" slug="invest" assistant={installed([bound])} />);
    const row = await screen.findByRole("group", { name: "Launch portfolio_review" });
    expect(within(row).getByText("Bound to review in reviews")).toBeInTheDocument();
    fireEvent.click(within(row).getByRole("button", { name: "Switch off" }));
    expect(await within(row).findByText(/needs the right to create work items/)).toBeInTheDocument();
    expect(mocks.bindLaunch).toHaveBeenCalledWith("varga", "invest", "portfolio_review", {
      project: "reviews",
      workflow: "review",
      enabled: false,
    });
  });

  it("draws nothing for an assistant without launches", () => {
    const { container } = wrap(<InstallationLaunches org="varga" slug="invest" assistant={installed([])} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("a launch in a conversation", () => {
  const proposed = {
    id: "a1",
    conversation_id: "c1",
    need: "portfolio_review",
    operation: "start",
    kind: "launch",
    arguments: { title: "Review of growth", inputs: { portfolio: "growth" } },
    summary: "start `review` in `reviews` (portfolio_review): Review of growth",
    status: "proposed",
    target: { project: "reviews", workflow: "review" },
    created_at: "2026-11-12T08:00:00Z",
  };

  beforeEach(() => {
    mocks.conversation.mockResolvedValue({ id: "c1", title: "Reviews", agent: "analyst", turns: 1, messages: [] });
  });

  it("is a card to confirm, which says the work is filed in the person's name", async () => {
    mocks.conversationActions.mockResolvedValue({ items: [proposed] });
    mocks.confirmAction.mockResolvedValue({});
    wrap(<ConversationView id="c1" />);
    expect(await screen.findByText("The assistant proposes to start a workflow")).toBeInTheDocument();
    expect(screen.getByText("review · reviews")).toBeInTheDocument();
    expect(screen.getByText(/files the work item in your name/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(mocks.confirmAction).toHaveBeenCalledWith("c1", "a1"));
  });

  it("links the work item once it is started", async () => {
    const executed = {
      ...proposed,
      status: "executed",
      result: { project: "reviews", workflow: "review", work_item_id: "w1", work_item_key: "REV-7" },
    };
    mocks.conversationActions.mockResolvedValue({ items: [executed] });
    wrap(<ConversationView id="c1" />);
    expect(await screen.findByRole("link", { name: "REV-7" })).toHaveAttribute("href", "/p/reviews/items/w1");
    expect(screen.queryByText("The assistant proposes to start a workflow")).not.toBeInTheDocument();
  });
});
