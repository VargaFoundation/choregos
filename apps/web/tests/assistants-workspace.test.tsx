import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AssistantWorkspace } from "@/components/assistants/assistant-workspace";
import { ConnectAccountRedirect } from "@/components/assistants/connected-accounts";
import { ApiError } from "@/lib/api";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), usePathname: () => "/assistants/varga/invest" }));

const mocks = vi.hoisted(() => ({
  myAssistants: vi.fn(),
  memory: vi.fn(),
  memoryAction: vi.fn(),
  deleteMemory: vi.fn(),
  eraseMyData: vi.fn(),
  connectedAccounts: vi.fn(),
  connectAccount: vi.fn(),
  disconnectAccount: vi.fn(),
  task: vi.fn(),
  putTask: vi.fn(),
  deleteTask: vi.fn(),
  runTask: vi.fn(),
  inbox: vi.fn(),
}));
vi.mock("@/lib/assistants-api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/assistants-api")>();
  return { ...real, assistantsApi: { ...real.assistantsApi, ...mocks } };
});

function wrap(ui: React.ReactNode) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui}</QueryClientProvider>,
  );
}

const invest = {
  org: "varga",
  slug: "invest",
  name: "investment-assistant",
  display_name: "Investment assistant",
  description: "Explains markets",
  status: "ready",
  memory_spaces: [
    { name: "preferred_sources", scope: "user", kind: "facts" },
    { name: "instrument_notes", scope: "user_subject", kind: "facts" },
  ],
  tasks: [
    { name: "morning_brief", delegation: "read_only", schedulable: true },
    { name: "threshold_alert", delegation: "none", schedulable: false },
  ],
  needs: [
    { need: "news", identity: "service", access: "read", required: true },
    { need: "bank_accounts", identity: "user_consent", access: "read", required: false },
  ],
};

const tab = (name: string) => fireEvent.click(screen.getByRole("tab", { name }));

beforeEach(() => {
  vi.clearAllMocks();
  mocks.myAssistants.mockResolvedValue({ items: [invest] });
  mocks.memory.mockImplementation(async (_o: string, _s: string, space: string) =>
    space === "preferred_sources"
      ? {
          space,
          scope: "user",
          kind: "facts",
          writes: "agent_proposes",
          subjects: [
            {
              subject: null,
              facts: [
                { id: "f1", content: "Prefers Reuters", status: "proposed", pinned: false },
                { id: "f2", content: "Likes charts", status: "confirmed", pinned: true },
              ],
              profiles: [{ version: 1, status: "confirmed", document: { horizon_years: 5 } }],
            },
          ],
        }
      : {
          space,
          scope: "user_subject",
          kind: "facts",
          writes: "agent_proposes",
          subjects: [{ subject: { type: "instrument", key: "FR001" }, facts: [{ id: "n1", content: "Watch the coupon", status: "confirmed", pinned: false }], profiles: [] }],
        },
  );
});

describe("one assistant, for me", () => {
  it("says when the assistant is not mine", async () => {
    mocks.myAssistants.mockResolvedValue({ items: [] });
    wrap(<AssistantWorkspace org="varga" slug="invest" />);
    expect(await screen.findByText("This assistant is not available to you.")).toBeInTheDocument();
  });

  it("shows what it remembers, per space and per subject, and acts on each fact", async () => {
    mocks.memoryAction.mockResolvedValue({});
    mocks.deleteMemory.mockResolvedValue(undefined);
    wrap(<AssistantWorkspace org="varga" slug="invest" />);
    expect(await screen.findByText("Prefers Reuters")).toBeInTheDocument();
    expect(screen.getByText("instrument: FR001")).toBeInTheDocument();
    expect(screen.getByText("Watch the coupon")).toBeInTheDocument();
    expect(screen.getByText(/horizon_years/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() =>
      expect(mocks.memoryAction).toHaveBeenCalledWith("varga", "invest", "preferred_sources", { action: "confirm", fact_id: "f1" }, undefined),
    );
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    await waitFor(() => expect(mocks.memoryAction).toHaveBeenCalledWith("varga", "invest", "preferred_sources", { action: "reject", fact_id: "f1" }, undefined));
    fireEvent.click(screen.getByRole("button", { name: "Unpin" }));
    await waitFor(() => expect(mocks.memoryAction).toHaveBeenCalledWith("varga", "invest", "preferred_sources", { action: "unpin", fact_id: "f2" }, undefined));
    fireEvent.click(screen.getAllByRole("button", { name: "Pin" })[0]!);
    await waitFor(() => expect(mocks.memoryAction).toHaveBeenCalledWith("varga", "invest", "preferred_sources", { action: "pin", fact_id: "f1" }, undefined));

    fireEvent.click(screen.getAllByRole("button", { name: "Delete" })[2]!);
    await waitFor(() => expect(mocks.deleteMemory).toHaveBeenCalledWith("varga", "invest", "instrument_notes", "n1", "FR001"));
  });

  it("shows a refused decision, forgets a whole space after a confirmation, and exports it", async () => {
    mocks.memoryAction.mockRejectedValueOnce(new Error("proposal expired"));
    mocks.deleteMemory.mockResolvedValue(undefined);
    const create = vi.fn(() => "blob:x");
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: create, revokeObjectURL: vi.fn() }));
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    wrap(<AssistantWorkspace org="varga" slug="invest" />);
    fireEvent.click(await screen.findByRole("button", { name: "Confirm" }));
    expect(await screen.findByText("proposal expired")).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole("button", { name: "Export" })[0]!);
    expect(create).toHaveBeenCalled();
    expect(click).toHaveBeenCalled();

    const space = screen.getByText("preferred sources").closest("section")!;
    fireEvent.click(within(space).getByRole("button", { name: "Forget all" }));
    fireEvent.click(within(space).getByRole("button", { name: "Forget all" }));
    await waitFor(() => expect(mocks.deleteMemory).toHaveBeenCalledWith("varga", "invest", "preferred_sources", undefined, undefined));
  });

  it("erases everything about me after a confirmation", async () => {
    mocks.eraseMyData.mockResolvedValue({ conversations: 3, memory_stores: 1, memory_facts: 7, memory_profiles: 0 });
    wrap(<AssistantWorkspace org="varga" slug="invest" />);
    await screen.findByText("Prefers Reuters");
    fireEvent.click(screen.getByRole("button", { name: "Erase everything about me" }));
    fireEvent.click(screen.getByRole("button", { name: "Erase my data" }));
    expect(await screen.findByText("Erased 3 conversations and 7 remembered facts.")).toBeInTheDocument();
  });

  it("lists my connected accounts of this organisation, reconnects and revokes", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    mocks.connectedAccounts.mockResolvedValue({
      items: [
        { org: "varga", connector: "bank", mode: "user_consent", scopes: ["read"], subject: "me@bank", status: "reconnect", last_used_at: "2026-11-09T10:00:00Z", created_at: "2026-11-01T00:00:00Z" },
        { org: "varga", connector: "broker", mode: "user_consent", scopes: [], status: "connected", created_at: "2026-11-01T00:00:00Z" },
        { org: "other", connector: "elsewhere", mode: "user_consent", scopes: [], status: "connected", created_at: "2026-11-01T00:00:00Z" },
      ],
    });
    mocks.connectAccount.mockResolvedValue({ authorization_url: "https://bank.example/consent?x=1" });
    mocks.disconnectAccount.mockResolvedValue(undefined);
    wrap(<AssistantWorkspace org="varga" slug="invest" />);
    await screen.findByText("Prefers Reuters");
    tab("Connected accounts");
    expect(await screen.findByText("Reconnect needed")).toBeInTheDocument();
    expect(screen.queryByText("elsewhere")).not.toBeInTheDocument();
    expect(screen.getByText("as me@bank")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Reconnect" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://bank.example/consent?x=1"));
    expect(mocks.connectAccount).toHaveBeenCalledWith("bank", "varga");

    fireEvent.click(screen.getAllByRole("button", { name: "Revoke" })[1]!);
    fireEvent.click(screen.getAllByRole("button", { name: "Revoke" })[1]!);
    await waitFor(() => expect(mocks.disconnectAccount).toHaveBeenCalledWith("broker", "varga"));

    fireEvent.change(screen.getByLabelText("Connector to connect"), { target: { value: "wealth" } });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    await waitFor(() => expect(mocks.connectAccount).toHaveBeenCalledWith("wealth", "varga"));
    vi.unstubAllGlobals();
  });

  it("shows a refusal to connect", async () => {
    mocks.connectedAccounts.mockResolvedValue({ items: [] });
    mocks.connectAccount.mockRejectedValue(new Error("this connector asks for no consent"));
    wrap(<AssistantWorkspace org="varga" slug="invest" />);
    await screen.findByText("Prefers Reuters");
    tab("Connected accounts");
    expect(await screen.findByText("No account connected")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Connector to connect"), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    expect(await screen.findByText("this connector asks for no consent")).toBeInTheDocument();
  });

  it("subscribes to a task on a cron and a time zone, runs it now, and unsubscribes", async () => {
    mocks.task.mockRejectedValue(new ApiError(404, { title: "not subscribed" }));
    mocks.putTask.mockResolvedValue({});
    mocks.runTask.mockResolvedValue({ work_item_id: "w", key: "TASK-1", project: "p" });
    mocks.deleteTask.mockResolvedValue(undefined);
    wrap(<AssistantWorkspace org="varga" slug="invest" />);
    await screen.findByText("Prefers Reuters");
    tab("Tasks");
    const card = (await screen.findByText("morning brief")).closest("section")!;
    await within(card).findByText("Not subscribed");
    fireEvent.change(within(card).getByLabelText("Runs"), { target: { value: "schedule" } });
    fireEvent.change(within(card).getByLabelText("Cron"), { target: { value: "30 7 * * 1-5" } });
    fireEvent.change(within(card).getByLabelText("Time zone"), { target: { value: "Europe/Paris" } });
    fireEvent.click(within(card).getByRole("button", { name: "Subscribe" }));
    await waitFor(() =>
      expect(mocks.putTask).toHaveBeenCalledWith("varga", "invest", "morning_brief", { kind: "schedule", cron: "30 7 * * 1-5", timezone: "Europe/Paris", enabled: true }),
    );
    fireEvent.click(within(card).getByRole("button", { name: "Run now" }));
    expect(await within(card).findByText(/Started: TASK-1/)).toBeInTheDocument();
  });

  it("shows an existing subscription, its refusal, and pauses or ends it", async () => {
    mocks.putTask.mockResolvedValue({});
    mocks.deleteTask.mockResolvedValue(undefined);
    mocks.task.mockResolvedValue({ task: "morning_brief", trigger: { kind: "schedule", cron: "30 7 * * 1-5", timezone: "Europe/Paris" }, delegation: "read_only", deliver: ["inbox"], enabled: true, last_run_at: "2026-11-10T07:30:00Z", last_refusal: "no consent", created_at: "2026-11-01T00:00:00Z" });
    wrap(<AssistantWorkspace org="varga" slug="invest" />);
    await screen.findByText("Prefers Reuters");
    tab("Tasks");
    expect(await screen.findAllByText("30 7 * * 1-5 (Europe/Paris)")).not.toHaveLength(0);
    expect((await screen.findAllByText(/The last run was refused: no consent/)).length).toBeGreaterThan(0);
    fireEvent.click((await screen.findAllByRole("button", { name: "Pause" }))[0]!);
    await waitFor(() => expect(mocks.putTask).toHaveBeenCalledWith("varga", "invest", "morning_brief", { enabled: false }));
    fireEvent.click((await screen.findAllByRole("button", { name: "Unsubscribe" }))[0]!);
    await waitFor(() => expect(mocks.deleteTask).toHaveBeenCalledWith("varga", "invest", "morning_brief"));
  });

  it("shows a refused task action", async () => {
    mocks.task.mockRejectedValue(new ApiError(404, { title: "not subscribed" }));
    mocks.runTask.mockRejectedValue(new Error("not ready"));
    wrap(<AssistantWorkspace org="varga" slug="invest" />);
    await screen.findByText("Prefers Reuters");
    tab("Tasks");
    fireEvent.click((await screen.findAllByRole("button", { name: "Run now" }))[0]!);
    expect(await screen.findByText("not ready")).toBeInTheDocument();
  });

  it("shows what the tasks delivered to me, for this assistant only", async () => {
    mocks.inbox.mockResolvedValue({
      items: [
        { id: "i1", task: "morning_brief", installation: "invest", work_item_id: "w", name: "Brief", version: 1, type: "report", media_type: "text/markdown", content: "# Good **morning**", created_at: "2026-11-10T07:31:00Z" },
        { id: "i2", task: "threshold_alert", installation: "invest", work_item_id: "w", name: "Alert", version: 1, type: "data", media_type: "application/json", content: { change: 4 }, created_at: "2026-11-10T07:31:00Z" },
        { id: "i3", task: "x", installation: "other", work_item_id: "w", name: "Not mine", version: 1, type: "data", media_type: "text/plain", content: "x", created_at: "2026-11-10T07:31:00Z" },
      ],
    });
    wrap(<AssistantWorkspace org="varga" slug="invest" />);
    await screen.findByText("Prefers Reuters");
    tab("Inbox");
    expect(await screen.findByText("morning", { selector: "strong" })).toBeInTheDocument();
    expect(screen.getByText(/"change": 4/)).toBeInTheDocument();
    expect(screen.queryByText("Not mine")).not.toBeInTheDocument();
  });

  it("says which needs are on: always on, optional, and under whose identity", async () => {
    wrap(<AssistantWorkspace org="varga" slug="invest" />);
    await screen.findByText("Prefers Reuters");
    tab("Sources");
    expect(await screen.findByText("Always on")).toBeInTheDocument();
    expect(screen.getByText("Optional")).toBeInTheDocument();
    expect(screen.getByText(/Acts as your connected account/)).toBeInTheDocument();
    expect(screen.getByText(/turns on once you connect it/)).toBeInTheDocument();
  });

  it("says when an assistant has no memory, no task and no source", async () => {
    mocks.myAssistants.mockResolvedValue({ items: [{ ...invest, memory_spaces: [], tasks: [], needs: [] }] });
    mocks.inbox.mockResolvedValue({ items: [] });
    wrap(<AssistantWorkspace org="varga" slug="invest" />);
    expect(await screen.findByText("No memory")).toBeInTheDocument();
    tab("Tasks");
    expect(screen.getByText("No task")).toBeInTheDocument();
    tab("Sources");
    expect(screen.getByText("No source")).toBeInTheDocument();
    tab("Inbox");
    expect(await screen.findByText("Nothing delivered")).toBeInTheDocument();
  });
});

describe("where a connect link lands", () => {
  it("asks for the authorization URL and goes there", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    mocks.connectAccount.mockResolvedValue({ authorization_url: "https://bank.example/consent" });
    render(<ConnectAccountRedirect connector="bank" org="varga" />);
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://bank.example/consent"));
    vi.unstubAllGlobals();
  });

  it("says what went wrong, or that the link names no organisation", async () => {
    mocks.connectAccount.mockRejectedValue(new Error("unknown connector"));
    const first = render(<ConnectAccountRedirect connector="bank" org="varga" />);
    expect(await screen.findByText("unknown connector")).toBeInTheDocument();
    first.unmount();
    render(<ConnectAccountRedirect connector="bank" org="" />);
    expect(screen.getByText("The link names no organisation.")).toBeInTheDocument();
  });
});
