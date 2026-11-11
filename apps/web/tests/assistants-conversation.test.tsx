import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AssistantsList } from "@/components/assistants/assistants-list";
import { ConversationList } from "@/components/assistants/conversation-list";
import { ConversationView } from "@/components/assistants/conversation-view";

const push = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }), usePathname: () => "/assistants" }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "varga", me: { memberships: [] } }) }));

const mocks = vi.hoisted(() => ({
  myAssistants: vi.fn(),
  openConversation: vi.fn(),
  myConversations: vi.fn(),
  deleteConversation: vi.fn(),
  conversation: vi.fn(),
  conversationActions: vi.fn(),
  confirmAction: vi.fn(),
  refuseAction: vi.fn(),
  cancelTurn: vi.fn(),
  feedback: vi.fn(),
  memory: vi.fn(),
  memoryAction: vi.fn(),
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

const assistant = (status = "ready") => ({
  org: "varga",
  slug: "invest",
  name: "investment-assistant",
  display_name: "Investment assistant",
  description: "Explains markets",
  status,
  memory_spaces: [{ name: "preferred_sources", scope: "user", kind: "facts" }],
  tasks: [],
  needs: [],
});

const summary = { id: "c1", title: "Rates", agent: "analyst", agent_version: 1, status: "open", turns: 2, cost_usd: 0.12, created_at: "2026-11-10T08:00:00Z", last_activity_at: "2026-11-10T08:05:00Z" };

beforeEach(() => {
  vi.clearAllMocks();
  mocks.myAssistants.mockResolvedValue({ items: [assistant()] });
  mocks.conversationActions.mockResolvedValue({ items: [] });
  mocks.memory.mockResolvedValue({ space: "preferred_sources", scope: "user", kind: "facts", writes: "agent_proposes", subjects: [] });
});

describe("the assistants of my audience", () => {
  it("lists them and opens a conversation", async () => {
    mocks.openConversation.mockResolvedValue({ ...summary, id: "c9" });
    wrap(<AssistantsList />);
    expect(await screen.findByText("Investment assistant")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Converse" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/conversations/c9"));
    expect(mocks.openConversation).toHaveBeenCalledWith("varga", "invest");
    expect(screen.getByRole("link", { name: "Memory, accounts and tasks" })).toHaveAttribute("href", "/assistants/varga/invest");
  });

  it("does not offer to converse with an assistant that is not ready, and says when there is none", async () => {
    mocks.myAssistants.mockResolvedValue({ items: [assistant("incomplete")] });
    const first = wrap(<AssistantsList />);
    expect(await screen.findByRole("button", { name: "Converse" })).toBeDisabled();
    first.unmount();
    mocks.myAssistants.mockResolvedValue({ items: [] });
    wrap(<AssistantsList />);
    expect(await screen.findByText("No assistant yet")).toBeInTheDocument();
  });

  it("shows a refusal to open", async () => {
    mocks.openConversation.mockRejectedValue(new Error("not in the audience"));
    wrap(<AssistantsList />);
    fireEvent.click(await screen.findByRole("button", { name: "Converse" }));
    expect(await screen.findByText("not in the audience")).toBeInTheDocument();
  });
});

describe("my conversations", () => {
  it("lists and deletes", async () => {
    mocks.myConversations.mockResolvedValue({ items: [summary], meta: { has_more: false } });
    mocks.deleteConversation.mockResolvedValue(undefined);
    wrap(<ConversationList />);
    expect(await screen.findByRole("link", { name: "Rates" })).toHaveAttribute("href", "/conversations/c1");
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(mocks.deleteConversation).toHaveBeenCalledWith("c1"));
  });

  it("says when there is none", async () => {
    mocks.myConversations.mockResolvedValue({ items: [], meta: { has_more: false } });
    wrap(<ConversationList />);
    expect(await screen.findByText("No conversation yet")).toBeInTheDocument();
  });
});

describe("a conversation", () => {
  const said = {
    ...summary,
    messages: [
      { role: "user", content: "Why did rates move?" },
      { role: "assistant", content: "**Rates rose** [Investment policy, 3]. Connect: /me/connected-accounts/bank/connect?org=varga" },
    ],
  };

  it("renders markdown, citations, a connect link, and sends a thumb with its comment", async () => {
    mocks.conversation.mockResolvedValue(said);
    mocks.feedback.mockResolvedValue({});
    wrap(<ConversationView id="c1" />);
    expect(await screen.findByText("Rates rose", { selector: "strong" })).toBeInTheDocument();
    expect(screen.getByLabelText("Citations")).toHaveTextContent("Investment policy · v3");
    expect(screen.getByRole("link", { name: "Connect your bank account" })).toHaveAttribute(
      "href",
      "/me/connected-accounts/bank/connect?org=varga",
    );
    fireEvent.click(screen.getByRole("button", { name: "Bad answer" }));
    fireEvent.change(screen.getByLabelText("Comment"), { target: { value: "wrong rate" } });
    fireEvent.click(screen.getByRole("button", { name: "Send feedback" }));
    await waitFor(() => expect(mocks.feedback).toHaveBeenCalledWith("varga", "c1", { rating: "down", comment: "wrong rate" }));
    expect(await screen.findByText("Thank you for your feedback.")).toBeInTheDocument();
  });

  it("sends the feedback to the conversation's organisation, not the session's", async () => {
    mocks.conversation.mockResolvedValue({ ...said, org: "acme" });
    mocks.feedback.mockResolvedValue({});
    wrap(<ConversationView id="c1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Good answer" }));
    fireEvent.click(screen.getByRole("button", { name: "Send feedback" }));
    await waitFor(() => expect(mocks.feedback).toHaveBeenCalledWith("acme", "c1", { rating: "up", comment: null }));
  });

  it("shows the memory proposals of the assistant the conversation carries, with no hint in the link", async () => {
    mocks.conversation.mockResolvedValue({ ...summary, org: "varga", assistant: "invest", messages: [] });
    mocks.memory.mockResolvedValue({
      space: "preferred_sources",
      scope: "user",
      kind: "facts",
      writes: "agent_proposes",
      subjects: [{ subject: null, facts: [{ id: "f1", content: "Prefers Reuters", status: "proposed", pinned: false }], profiles: [] }],
    });
    wrap(<ConversationView id="c1" />);
    expect(await screen.findByText("Prefers Reuters")).toBeInTheDocument();
  });

  it("shows a refused thumb", async () => {
    mocks.conversation.mockResolvedValue(said);
    mocks.feedback.mockRejectedValue(new Error("session closed"));
    wrap(<ConversationView id="c1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Good answer" }));
    fireEvent.click(screen.getByRole("button", { name: "Send feedback" }));
    expect(await screen.findByText("session closed")).toBeInTheDocument();
  });

  it("streams the answer as it comes, then reads the conversation again", async () => {
    mocks.conversation.mockResolvedValue({ ...summary, messages: [] });
    let finish: () => void = () => undefined;
    mocks.streamTurn.mockImplementation(
      async (_id: string, _text: string, handlers: { onDelta: (t: string) => void; onDone: (a: { status: string }) => void }) => {
        handlers.onDelta("Hello ");
        handlers.onDelta("**world**");
        await new Promise<void>((resolve) => (finish = resolve));
        handlers.onDone({ status: "completed" });
      },
    );
    wrap(<ConversationView id="c1" />);
    await screen.findByLabelText("Message");
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "hi" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("world", { selector: "strong" })).toBeInTheDocument();
    expect(mocks.streamTurn).toHaveBeenCalledWith("c1", "hi", expect.anything(), expect.anything());
    mocks.conversation.mockResolvedValue({ ...summary, messages: [{ role: "user", content: "hi" }, { role: "assistant", content: "Hello **world**" }] });
    finish();
    await waitFor(() => expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument());
    expect(mocks.conversation).toHaveBeenCalledTimes(2);
  });

  it("cancels a running turn", async () => {
    mocks.conversation.mockResolvedValue({ ...summary, messages: [] });
    mocks.cancelTurn.mockResolvedValue(summary);
    mocks.streamTurn.mockImplementation(
      (_id: string, _text: string, _handlers: unknown, signal: AbortSignal) =>
        new Promise((_resolve, reject) => signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")))),
    );
    wrap(<ConversationView id="c1" />);
    fireEvent.change(await screen.findByLabelText("Message"), { target: { value: "hi" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(mocks.cancelTurn).toHaveBeenCalledWith("c1"));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows a failed turn and the accounts an answer asks to connect", async () => {
    mocks.conversation.mockResolvedValue({ ...summary, messages: [] });
    mocks.streamTurn.mockImplementation(
      async (_i: string, _t: string, h: { onDone: (a: unknown) => void; onError: (m: string) => void }) => {
        h.onDone({ status: "failed", missing_accounts: [{ need: "bank", connect_url: "https://x/me/connected-accounts/bank/connect?org=varga" }] });
        h.onError("the turn failed");
      },
    );
    wrap(<ConversationView id="c1" />);
    fireEvent.change(await screen.findByLabelText("Message"), { target: { value: "hi" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("the turn failed")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Connect your bank account" })).toBeInTheDocument();
  });

  it("shows a refusal raised before the stream", async () => {
    mocks.conversation.mockResolvedValue({ ...summary, messages: [] });
    mocks.streamTurn.mockRejectedValue(new Error("not in the audience any more"));
    wrap(<ConversationView id="c1" />);
    fireEvent.change(await screen.findByLabelText("Message"), { target: { value: "hi" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("not in the audience any more")).toBeInTheDocument();
  });

  it("shows a write as a card to confirm, and another to refuse", async () => {
    mocks.conversation.mockResolvedValue({ ...summary, messages: [] });
    const action = (id: string) => ({ id, conversation_id: "c1", need: "orders", operation: "place_order", arguments: { isin: "FR001" }, summary: `Buy ${id}`, status: "proposed", created_at: "2026-11-10T08:00:00Z" });
    mocks.conversationActions.mockResolvedValue({ items: [action("a1"), action("a2"), { ...action("a3"), status: "executed" }] });
    mocks.confirmAction.mockResolvedValue({});
    mocks.refuseAction.mockRejectedValue(new Error("already decided"));
    wrap(<ConversationView id="c1" />);
    expect(await screen.findByText("Buy a1")).toBeInTheDocument();
    expect(screen.queryByText("Buy a3")).not.toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "Confirm" })[0]!);
    await waitFor(() => expect(mocks.confirmAction).toHaveBeenCalledWith("c1", "a1"));
    fireEvent.click(screen.getAllByRole("button", { name: "Refuse" })[1]!);
    await waitFor(() => expect(mocks.refuseAction).toHaveBeenCalledWith("c1", "a2"));
    expect(await screen.findByText("already decided")).toBeInTheDocument();
  });

  it("shows the diff the source previewed on a write's card, and none when there is none", async () => {
    mocks.conversation.mockResolvedValue({ ...summary, messages: [] });
    const action = (id: string, diff: unknown) => ({ id, conversation_id: "c1", need: "orders", operation: "place_order", arguments: {}, summary: `Buy ${id}`, status: "proposed", diff, created_at: "2026-11-10T08:00:00Z" });
    mocks.conversationActions.mockResolvedValue({ items: [action("a1", { cash: { before: 100, after: 40 } }), action("a2", null)] });
    wrap(<ConversationView id="c1" />);
    await screen.findByText("Buy a1");
    const diffs = screen.getAllByTestId("action-diff");
    expect(diffs).toHaveLength(1);
    expect(diffs[0]).toHaveTextContent('"after": 40');
  });

  it("shows what the assistant proposes to remember, and decides it", async () => {
    mocks.conversation.mockResolvedValue({ ...summary, messages: [] });
    mocks.memory.mockResolvedValue({
      space: "preferred_sources",
      scope: "user",
      kind: "facts",
      writes: "agent_proposes",
      subjects: [
        {
          subject: null,
          facts: [
            { id: "f1", content: "Prefers Reuters", status: "proposed", pinned: false },
            { id: "f2", content: "Already known", status: "confirmed", pinned: false },
          ],
          profiles: [{ version: 2, status: "proposed", document: { risk_tolerance: "low" } }],
        },
      ],
    });
    mocks.memoryAction.mockResolvedValue({});
    wrap(<ConversationView id="c1" assistant="varga/invest" />);
    await screen.findByText("Prefers Reuters");
    const cards = screen.getByTestId("memory-proposals");
    expect(cards).toHaveTextContent("Prefers Reuters");
    expect(cards).not.toHaveTextContent("Already known");
    expect(cards).toHaveTextContent("risk_tolerance");
    const buttons = cards.querySelectorAll("button");
    fireEvent.click(buttons[0]!);
    await waitFor(() =>
      expect(mocks.memoryAction).toHaveBeenCalledWith("varga", "invest", "preferred_sources", { action: "confirm", fact_id: "f1" }, undefined),
    );
    fireEvent.click(buttons[3]!);
    await waitFor(() =>
      expect(mocks.memoryAction).toHaveBeenCalledWith("varga", "invest", "preferred_sources", { action: "reject", version: 2 }, undefined),
    );
  });
});
