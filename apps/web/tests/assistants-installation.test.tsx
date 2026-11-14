import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { InstallAssistant, groupsOf } from "@/components/assistants/install-assistant";
import { InstallationDetail } from "@/components/assistants/installation-detail";
import { InstallationsList } from "@/components/assistants/installations-list";
import { api, ApiError } from "@/lib/api";
import { mockCostReport } from "@/mocks/data";

const push = vi.hoisted(() => vi.fn());
const roles = vi.hoisted(() => ({ admin: true }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }), usePathname: () => "/assistants/manage" }));
vi.mock("@/lib/session", () => ({
  useSession: () => ({
    org: "varga",
    me: { memberships: roles.admin ? [{ org: "varga", role: "org_admin", project_slug: null }] : [] },
  }),
}));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      plugins: vi.fn(),
      orgConnectors: vi.fn(),
      knowledgeBases: vi.fn(),
      agents: vi.fn(),
      orgCosts: vi.fn(),
    },
  };
});

const mocks = vi.hoisted(() => ({
  pluginAssistants: vi.fn(),
  installationMemory: vi.fn(),
  installationMemoryAction: vi.fn(),
  installations: vi.fn(),
  installation: vi.fn(),
  install: vi.fn(),
  update: vi.fn(),
  uninstall: vi.fn(),
  readiness: vi.fn(),
  bind: vi.fn(),
  bindKnowledge: vi.fn(),
  knowledgeUpdates: vi.fn(),
  decideKnowledgeUpdate: vi.fn(),
  reads: vi.fn(),
  eraseSubject: vi.fn(),
  feedbackCandidates: vi.fn(),
  webhookSecret: vi.fn(),
  taskEventKey: vi.fn(),
  taskEvents: vi.fn(),
  teamsDelivery: vi.fn(),
  putTeamsDelivery: vi.fn(),
  deleteTeamsDelivery: vi.fn(),
  acceptCandidate: vi.fn(),
  dismissCandidate: vi.fn(),
  stop: vi.fn(),
  resume: vi.fn(),
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

const shipped = {
  items: [
    {
      name: "investment-assistant",
      display_name: "Investment assistant",
      description: "Explains markets",
      config_schema: { type: "object", required: ["house_name"], properties: { house_name: { type: "string" } } },
      suggested_groups: ["grp-investors"],
      needs: [
        { need: "news", identity: "service", access: "read", required: true },
        { need: "orders", identity: "user_consent", access: "write", required: false },
      ],
    },
  ],
};

const installed = (extra: Record<string, unknown> = {}) => ({
  slug: "invest",
  plugin: "investment-assistant",
  plugin_version: "1.0.0",
  assistant_name: "investment-assistant",
  display_name: "Investment assistant",
  config: { house_name: "Varga" },
  owner_groups: ["owners"],
  audience_groups: ["investors"],
  channel: "stable",
  status: "incomplete",
  policy_overrides: { budget: { per_user_month_usd: 20 } },
  bindings: [],
  created_at: "2026-11-10T08:00:00Z",
  ...extra,
});

beforeEach(() => {
  vi.clearAllMocks();
  roles.admin = true;
  vi.mocked(api.plugins).mockResolvedValue({ items: [{ name: "investment-assistant", version: "1.0.0" }] } as never);
  vi.mocked(api.orgConnectors).mockResolvedValue([{ name: "news-feed" }, { name: "broker" }] as never);
  vi.mocked(api.agents).mockResolvedValue([{ slug: "investment-analyst" }] as never);
  mocks.pluginAssistants.mockResolvedValue(shipped);
  mocks.installation.mockResolvedValue(installed());
  mocks.readiness.mockResolvedValue({
    status: "incomplete",
    items: [{ kind: "need_unbound", need: "news", key: null, message: "need `news` is not bound", fix_by: "owners", groups: [] }],
  });
  mocks.knowledgeUpdates.mockResolvedValue({ items: [] });
  mocks.reads.mockResolvedValue({ items: [], meta: { has_more: false } });
  mocks.feedbackCandidates.mockResolvedValue([]);
  mocks.taskEvents.mockResolvedValue({ items: [] });
  mocks.teamsDelivery.mockResolvedValue({ configured: false, version: null, updated_at: null });
  // The organisation's report from the mock ledger, filtered and grouped as the API does (S64-09b).
  vi.mocked(api.orgCosts).mockImplementation(async (org, group, filtre) =>
    mockCostReport(`/orgs/${org}/costs?${new URLSearchParams({ group_by: group ?? "day", ...(filtre as Record<string, string>) })}`),
  );
});

describe("installing an assistant", () => {
  it("splits a list of groups", () => {
    expect(groupsOf(" a, b ,,c")).toEqual(["a", "b", "c"]);
  });

  it("draws the configuration form from the config schema, suggests the audience, and installs", async () => {
    mocks.install.mockResolvedValue(installed());
    const done = vi.fn();
    wrap(<InstallAssistant org="varga" onInstalled={done} />);
    await screen.findByRole("option", { name: /investment-assistant 1.0.0/ });
    fireEvent.change(screen.getByLabelText("Plugin"), { target: { value: "investment-assistant" } });
    await screen.findByRole("option", { name: "Investment assistant" });
    fireEvent.change(screen.getByLabelText("Assistant"), { target: { value: "investment-assistant" } });
    expect(await screen.findByText("Explains markets")).toBeInTheDocument();
    expect(screen.getByLabelText("Audience groups")).toHaveValue("grp-investors");
    const install = screen.getByRole("button", { name: "Install" });
    expect(install).toBeDisabled();
    expect(screen.getByText("Required: house_name")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/house name/i), { target: { value: "Varga" } });
    fireEvent.change(screen.getByLabelText("Owner groups"), { target: { value: "owners, auditors" } });
    fireEvent.change(screen.getByLabelText("Channel"), { target: { value: "candidate" } });
    expect(install).toBeEnabled();
    fireEvent.click(install);
    await waitFor(() =>
      expect(mocks.install).toHaveBeenCalledWith("varga", {
        plugin: "investment-assistant",
        assistant: "investment-assistant",
        config: { house_name: "Varga" },
        owner_groups: ["owners", "auditors"],
        audience_groups: ["grp-investors"],
        channel: "candidate",
      }),
    );
    expect(done).toHaveBeenCalledWith("invest");
  });

  it("shows a refused installation, and a plugin without assistant", async () => {
    mocks.install.mockRejectedValue(new Error("config does not match"));
    wrap(<InstallAssistant org="varga" />);
    await screen.findByRole("option", { name: /investment-assistant 1.0.0/ });
    fireEvent.change(screen.getByLabelText("Plugin"), { target: { value: "investment-assistant" } });
    await screen.findByRole("option", { name: "Investment assistant" });
    fireEvent.change(screen.getByLabelText("Assistant"), { target: { value: "investment-assistant" } });
    fireEvent.change(await screen.findByLabelText(/house name/i), { target: { value: "V" } });
    fireEvent.change(screen.getByLabelText("Audience groups"), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "Install" }));
    expect(await screen.findByText("config does not match")).toBeInTheDocument();
    expect(mocks.install.mock.calls[0]![1].audience_groups).toEqual(["x"]);
  });

  it("says when no plugin is installed or the plugin ships no assistant", async () => {
    vi.mocked(api.plugins).mockResolvedValue({ items: [] } as never);
    const first = wrap(<InstallAssistant org="varga" />);
    expect(await screen.findByText(/Install a plugin that ships an assistant first/)).toBeInTheDocument();
    first.unmount();
    vi.mocked(api.plugins).mockResolvedValue({ items: [{ name: "empty", version: "1" }] } as never);
    mocks.pluginAssistants.mockResolvedValue({ items: [] });
    wrap(<InstallAssistant org="varga" />);
    await screen.findByRole("option", { name: /empty 1/ });
    fireEvent.change(screen.getByLabelText("Plugin"), { target: { value: "empty" } });
    expect(await screen.findByText("This plugin ships no assistant.")).toBeInTheDocument();
  });
});

describe("the installations", () => {
  it("lists what I manage, and offers the form to an administrator only", async () => {
    mocks.installations.mockResolvedValue({ items: [installed()] });
    const admin = wrap(<InstallationsList />);
    expect(await screen.findByRole("link", { name: "Open" })).toHaveAttribute("href", "/assistants/manage/invest");
    expect(await screen.findByText("Install an assistant")).toBeInTheDocument();
    admin.unmount();
    roles.admin = false;
    wrap(<InstallationsList />);
    await screen.findByRole("link", { name: "Open" });
    expect(screen.queryByText("Install an assistant")).not.toBeInTheDocument();
  });

  it("says when there is none", async () => {
    mocks.installations.mockResolvedValue({ items: [] });
    roles.admin = false;
    wrap(<InstallationsList />);
    expect(await screen.findByText("No installation")).toBeInTheDocument();
  });
});

describe("one installation, for its owners", () => {
  it("shows the readiness items and binds a need to a connector", async () => {
    mocks.bind.mockResolvedValue(installed());
    wrap(<InstallationDetail slug="invest" />);
    expect(await screen.findByText("need `news` is not bound")).toBeInTheDocument();
    expect(screen.getByText("to fix: the owners")).toBeInTheDocument();
    fireEvent.change(await screen.findByLabelText("Connector for news"), { target: { value: "news-feed" } });
    await waitFor(() => expect(mocks.bind).toHaveBeenCalledWith("varga", "invest", "news", { connector: "news-feed", enabled: true, approval: null }));
  });

  it("binds a knowledge need to bases the owner reads, keeps one another owner bound, and shows a refusal", async () => {
    mocks.pluginAssistants.mockResolvedValue({
      items: [
        {
          ...shipped.items[0],
          knowledge_needs: [{ name: "house_research", description: "The house's research notes.", required: true }],
        },
      ],
    });
    mocks.installation.mockResolvedValue(
      installed({ knowledge_bindings: [{ name: "house_research", bases: ["board-notes"], enabled: true }] }),
    );
    vi.mocked(api.knowledgeBases).mockResolvedValue([
      { slug: "house-notes", name: "House notes", scope: "org", serving_mode: "index", status: "active" },
      { slug: "old-notes", name: "Old notes", scope: "org", serving_mode: "index", status: "archived" },
    ] as never);
    mocks.bindKnowledge.mockRejectedValueOnce(new Error("`house-notes` is not a knowledge base of varga you may read"));
    mocks.bindKnowledge.mockResolvedValue(installed());
    wrap(<InstallationDetail slug="invest" />);
    expect(await screen.findByText("The house's research notes.")).toBeInTheDocument();
    // The base another owner bound stays listed and checked; an archived base is not offered.
    expect(await screen.findByLabelText("board-notes for house_research")).toBeChecked();
    expect(screen.queryByLabelText("old-notes for house_research")).not.toBeInTheDocument();
    fireEvent.click(await screen.findByLabelText("house-notes for house_research"));
    fireEvent.click(screen.getByRole("button", { name: "Bind house_research" }));
    expect(await screen.findByText("`house-notes` is not a knowledge base of varga you may read")).toBeInTheDocument();
    expect(mocks.bindKnowledge).toHaveBeenCalledWith("varga", "invest", "house_research", {
      bases: ["board-notes", "house-notes"],
    });
    fireEvent.click(screen.getByLabelText("board-notes for house_research"));
    fireEvent.click(screen.getByRole("button", { name: "Bind house_research" }));
    await waitFor(() =>
      expect(mocks.bindKnowledge).toHaveBeenLastCalledWith("varga", "invest", "house_research", { bases: ["house-notes"] }),
    );
  });

  it("shows no knowledge card for an assistant without a knowledge need", async () => {
    wrap(<InstallationDetail slug="invest" />);
    expect(await screen.findByText("need `news` is not bound")).toBeInTheDocument();
    expect(screen.queryByText("Knowledge from the organisation")).not.toBeInTheDocument();
  });

  it("lists the installation's own memory for its owners, decides proposals and adds a fact", async () => {
    mocks.pluginAssistants.mockResolvedValue({
      items: [
        {
          ...shipped.items[0],
          memory_spaces: [
            { name: "investor_profile", scope: "user", kind: "profile" },
            { name: "house_view", scope: "installation", kind: "facts" },
          ],
        },
      ],
    });
    mocks.installationMemory.mockResolvedValue({
      space: "house_view",
      scope: "installation",
      kind: "facts",
      writes: "owner_only",
      subjects: [
        {
          subject: null,
          facts: [
            { id: "h1", content: "Overweight bonds", status: "proposed", pinned: false },
            { id: "h2", content: "No tobacco", status: "active", pinned: false },
          ],
          profiles: [],
        },
      ],
    });
    mocks.installationMemoryAction.mockResolvedValue({});
    wrap(<InstallationDetail slug="invest" />);
    expect(await screen.findByText("Overweight bonds")).toBeInTheDocument();
    expect(mocks.installationMemory).toHaveBeenCalledTimes(1);
    expect(mocks.installationMemory).toHaveBeenCalledWith("varga", "invest", "house_view"); // a person's space is not listed
    const card = screen.getByText("Overweight bonds").closest("section")!;
    fireEvent.click(within(card).getByRole("button", { name: "Confirm" }));
    await waitFor(() =>
      expect(mocks.installationMemoryAction).toHaveBeenCalledWith("varga", "invest", "house_view", { action: "confirm", fact_id: "h1" }),
    );
    fireEvent.click(within(card).getByRole("button", { name: "Reject" }));
    await waitFor(() =>
      expect(mocks.installationMemoryAction).toHaveBeenCalledWith("varga", "invest", "house_view", { action: "reject", fact_id: "h1" }),
    );
    fireEvent.change(within(card).getByLabelText("Add a fact"), { target: { value: "Cap equities at 60%" } });
    fireEvent.click(within(card).getByRole("button", { name: "Add" }));
    await waitFor(() =>
      expect(mocks.installationMemoryAction).toHaveBeenCalledWith("varga", "invest", "house_view", { action: "add", content: "Cap equities at 60%" }),
    );
  });

  it("shows the webhook key once, and rotates it on request", async () => {
    mocks.webhookSecret
      .mockResolvedValueOnce({ secret: "chtask_first", generation: 0, shown_at: "2026-11-11T00:00:00Z" })
      .mockRejectedValueOnce(new Error("the webhook key was already shown: rotate it to get a new one"))
      .mockResolvedValueOnce({ secret: "chtask_second", generation: 1, shown_at: "2026-11-11T00:00:00Z" });
    wrap(<InstallationDetail slug="invest" />);
    fireEvent.click(await screen.findByRole("button", { name: "Show the key" }));
    expect(await screen.findByText("chtask_first")).toBeInTheDocument();
    expect(mocks.webhookSecret).toHaveBeenLastCalledWith("varga", "invest", false);
    fireEvent.click(screen.getByRole("button", { name: "Show the key" }));
    expect(await screen.findByText(/already shown/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Rotate the key" }));
    expect(await screen.findByText("chtask_second")).toBeInTheDocument();
    expect(mocks.webhookSecret).toHaveBeenLastCalledWith("varga", "invest", true);
  });

  it("reveals the URL and key of a trigger task's event door on each click, with a secret warning and a copy", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    mocks.taskEventKey
      .mockResolvedValueOnce({ url: "https://api.test/hook/new_ticket/events", secret: "chev_one", generation: 2 })
      .mockRejectedValueOnce(new Error("task has no trigger"));
    wrap(<InstallationDetail slug="invest" />);
    expect(await screen.findByText(/The key below is a secret/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Task"), { target: { value: "new_ticket" } });
    fireEvent.click(screen.getByRole("button", { name: "Show the URL and key" }));
    expect(await screen.findByText("chev_one")).toBeInTheDocument();
    expect(screen.getByText("https://api.test/hook/new_ticket/events")).toBeInTheDocument();
    expect(mocks.taskEventKey).toHaveBeenLastCalledWith("varga", "invest", "new_ticket");
    fireEvent.click(screen.getByRole("button", { name: "Copy the key" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("chev_one"));
    fireEvent.click(screen.getByRole("button", { name: "Show the URL and key" }));
    expect(await screen.findByText("task has no trigger")).toBeInTheDocument();
    expect(screen.queryByText("chev_one")).not.toBeInTheDocument();
  });

  it("lists the task events: outcome, summary, work item, time", async () => {
    mocks.taskEvents.mockResolvedValue({
      items: [
        { task: "new_ticket", event_id: "e2", outcome: "refused", summary: "no subscriber", work_item_key: null, received_at: "2026-11-11T09:00:00Z" },
        { task: "new_ticket", event_id: "e1", outcome: "started", summary: "run started", work_item_key: "WI-7", received_at: "2026-11-11T08:00:00Z" },
      ],
    });
    wrap(<InstallationDetail slug="invest" />);
    expect(await screen.findByText("no subscriber")).toBeInTheDocument();
    expect(screen.getByText("refused")).toBeInTheDocument();
    expect(screen.getByText("started")).toBeInTheDocument();
    expect(screen.getByText("WI-7")).toBeInTheDocument();
    const rows = screen.getAllByRole("row");
    expect(rows[1]).toHaveTextContent("no subscriber");
  });

  it("seals a Teams webhook without ever showing it, and removes it", async () => {
    mocks.teamsDelivery.mockResolvedValueOnce({ configured: false, version: null, updated_at: null });
    mocks.putTeamsDelivery.mockResolvedValue({ configured: true, version: 1, updated_at: "2026-11-11T08:00:00Z" });
    mocks.deleteTeamsDelivery.mockResolvedValue(undefined);
    wrap(<InstallationDetail slug="invest" />);
    expect(await screen.findByText("Not configured")).toBeInTheDocument();
    mocks.teamsDelivery.mockResolvedValue({ configured: true, version: 1, updated_at: "2026-11-11T08:00:00Z" });
    fireEvent.change(screen.getByLabelText("Teams webhook URL"), { target: { value: "https://example.webhook.office.com/abc" } });
    fireEvent.click(screen.getByRole("button", { name: "Seal the URL" }));
    await waitFor(() => expect(mocks.putTeamsDelivery).toHaveBeenCalledWith("varga", "invest", "https://example.webhook.office.com/abc"));
    expect(await screen.findByText(/version 1/)).toBeInTheDocument();
    expect(screen.getByLabelText("Teams webhook URL")).toHaveValue("");
    expect(document.body.textContent).not.toContain("example.webhook.office.com");
    mocks.teamsDelivery.mockResolvedValue({ configured: false, version: null, updated_at: null });
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    fireEvent.click(await screen.findByRole("button", { name: "Remove" }));
    await waitFor(() => expect(mocks.deleteTeamsDelivery).toHaveBeenCalledWith("varga", "invest"));
    expect(await screen.findByText("Not configured")).toBeInTheDocument();
  });

  it("sets the approval of a write need once bound, and shows a refused binding", async () => {
    mocks.installation.mockResolvedValue(
      installed({ bindings: [{ need: "orders", connector: "broker", operations: ["place_order"], identity: "user_consent", approval: "console", enabled: true }] }),
    );
    mocks.bind.mockRejectedValueOnce(new Error("the connector does not serve place_order"));
    wrap(<InstallationDetail slug="invest" />);
    fireEvent.change(await screen.findByLabelText("Approval for orders"), { target: { value: "inline" } });
    await waitFor(() => expect(mocks.bind).toHaveBeenCalledWith("varga", "invest", "orders", { connector: "broker", enabled: true, approval: "inline" }));
    expect(await screen.findByText("the connector does not serve place_order")).toBeInTheDocument();
  });

  it("saves the settings: configuration, groups, channel and budgets", async () => {
    mocks.update.mockResolvedValue(installed());
    wrap(<InstallationDetail slug="invest" />);
    fireEvent.change(await screen.findByLabelText("Audience groups"), { target: { value: "investors, partners" } });
    fireEvent.change(screen.getByLabelText("Per conversation (USD)"), { target: { value: "2.5" } });
    fireEvent.change(screen.getByLabelText("Channel"), { target: { value: "candidate" } });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    await waitFor(() =>
      expect(mocks.update).toHaveBeenCalledWith("varga", "invest", {
        config: { house_name: "Varga" },
        audience_groups: ["investors", "partners"],
        owner_groups: ["owners"],
        channel: "candidate",
        policy_overrides: { budget: { per_user_month_usd: 20, per_conversation_usd: 2.5 } },
      }),
    );
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
  });

  it("shows a refused update", async () => {
    mocks.update.mockRejectedValue(new Error("only the administrator changes the owners"));
    wrap(<InstallationDetail slug="invest" />);
    fireEvent.click(await screen.findByRole("button", { name: "Save settings" }));
    expect(await screen.findByText("only the administrator changes the owners")).toBeInTheDocument();
  });

  it("shows a knowledge update with its diff, and accepts, merges or refuses it", async () => {
    const update = (id: string) => ({
      id,
      base: "investment_policy",
      path: `policy-${id}.md`,
      kind: "update",
      diff: "--- a\n+++ b\n-old line\n+new line\n context",
      proposed_content: "new line",
      plugin_version: "1.1.0",
      status: "proposed",
      created_at: "2026-11-10T08:00:00Z",
    });
    mocks.knowledgeUpdates.mockResolvedValue({ items: [update("u1"), update("u2"), update("u3")] });
    mocks.decideKnowledgeUpdate.mockResolvedValueOnce({}).mockResolvedValueOnce({}).mockRejectedValueOnce(new Error("already decided"));
    wrap(<InstallationDetail slug="invest" />);
    const first = (await screen.findByText("policy-u1.md")).closest("section")!;
    expect(within(first).getByText("+new line")).toBeInTheDocument();
    fireEvent.click(within(first).getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(mocks.decideKnowledgeUpdate).toHaveBeenCalledWith("varga", "invest", "u1", { decision: "accept", content: null }));

    const second = screen.getByText("policy-u2.md").closest("section")!;
    fireEvent.click(within(second).getByRole("button", { name: "Merge with edits" }));
    fireEvent.change(within(second).getByLabelText("Merged content"), { target: { value: "merged text" } });
    fireEvent.click(within(second).getByRole("button", { name: "Save the merge" }));
    await waitFor(() => expect(mocks.decideKnowledgeUpdate).toHaveBeenCalledWith("varga", "invest", "u2", { decision: "merge", content: "merged text" }));

    const third = screen.getByText("policy-u3.md").closest("section")!;
    fireEvent.click(within(third).getByRole("button", { name: "Refuse" }));
    expect(await within(third).findByText("already decided")).toBeInTheDocument();
    expect(mocks.decideKnowledgeUpdate).toHaveBeenLastCalledWith("varga", "invest", "u3", { decision: "refuse", content: null });
  });

  it("reads the journal, filtered", async () => {
    mocks.reads.mockResolvedValue({
      items: [{ id: "r1", user_id: "u1", need: "market_data", operation: "get_quote", subject_type: "instrument", subject_key: "FR001", at: "2026-11-10T08:00:00Z" }],
      meta: { has_more: false },
    });
    wrap(<InstallationDetail slug="invest" />);
    expect(await screen.findByText("get_quote")).toBeInTheDocument();
    expect(screen.getByText("instrument: FR001")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("user id"), { target: { value: "u1" } });
    fireEvent.click(screen.getByRole("button", { name: "Filter" }));
    await waitFor(() => expect(mocks.reads).toHaveBeenLastCalledWith("varga", "invest", { subject_type: "", subject_key: "", user_id: "u1" }));
  });

  it("exports then erases by subject, after a confirmation", async () => {
    mocks.eraseSubject
      .mockResolvedValueOnce({ mode: "export", subject: { type: "instrument", key: "FR001" }, memories: [], conversations: [] })
      .mockResolvedValueOnce({ mode: "erase", subject: { type: "instrument", key: "FR001" }, memories_erased: 2, conversations_redacted: 1, conversations_deleted: 0 })
      .mockRejectedValueOnce(new Error("erasure is not declared"));
    wrap(<InstallationDetail slug="invest" />);
    const card = (await screen.findByText("Export or erase by subject")).closest("section")!;
    expect(within(card).getByRole("button", { name: "Export" })).toBeDisabled();
    fireEvent.change(within(card).getByLabelText("Subject type"), { target: { value: "instrument" } });
    fireEvent.change(within(card).getByLabelText("Subject key"), { target: { value: "FR001" } });
    fireEvent.click(within(card).getByRole("button", { name: "Export" }));
    expect(await within(card).findByLabelText("Export")).toHaveTextContent('"mode": "export"');
    expect(mocks.eraseSubject).toHaveBeenCalledWith("varga", "invest", { subject: { type: "instrument", key: "FR001" }, mode: "export", conversations: "redact" });

    fireEvent.change(within(card).getByLabelText("Conversations on erasure"), { target: { value: "delete" } });
    fireEvent.click(within(card).getByRole("button", { name: "Erase" }));
    fireEvent.click(within(card).getByRole("button", { name: "Erase" }));
    expect(await within(card).findByText(/Erased 2 memories; redacted 1 and deleted 0 conversations/)).toBeInTheDocument();
    fireEvent.click(within(card).getByRole("button", { name: "Export" }));
    expect(await within(card).findByText("erasure is not declared")).toBeInTheDocument();
  });

  it("lists the candidate cases of an agent's thumbs down", async () => {
    mocks.feedbackCandidates.mockResolvedValue([
      { id: "k1", agent: "investment-analyst", agent_version: 4, status: "proposed", candidate: { case: "why did it fall", comment: "wrong" }, created_at: "2026-11-10T08:00:00Z" },
    ]);
    wrap(<InstallationDetail slug="invest" />);
    await screen.findByRole("option", { name: "investment-analyst" });
    fireEvent.change(screen.getByLabelText("Agent"), { target: { value: "investment-analyst" } });
    expect(await screen.findByText(/why did it fall/)).toBeInTheDocument();
    expect(mocks.feedbackCandidates).toHaveBeenCalledWith("varga", "investment-analyst");
  });

  describe("deciding a candidate (S63-17)", () => {
    const candidate = {
      id: "k1",
      agent: "investment-analyst",
      agent_version: 4,
      status: "proposed",
      candidate: { case: { name: "candidate-1", turns: ["why did it fall"] } },
      created_at: "2026-11-10T08:00:00Z",
    };
    async function open() {
      mocks.feedbackCandidates.mockResolvedValue([candidate]);
      wrap(<InstallationDetail slug="invest" />);
      await screen.findByRole("option", { name: "investment-analyst" });
      fireEvent.change(screen.getByLabelText("Agent"), { target: { value: "investment-analyst" } });
      await screen.findByLabelText("Assertions");
    }

    it("adds it to the suite with the assertions the owner wrote", async () => {
      mocks.acceptCandidate.mockResolvedValue({ ...candidate, status: "accepted" });
      await open();
      fireEvent.change(screen.getByLabelText("Assertions"), { target: { value: '{"must_not_contain": ["Buy."]}' } });
      mocks.feedbackCandidates.mockResolvedValue([]);
      fireEvent.click(screen.getByRole("button", { name: "Add to the suite" }));
      await waitFor(() =>
        expect(mocks.acceptCandidate).toHaveBeenCalledWith("varga", "investment-analyst", "k1", {
          assertions: { must_not_contain: ["Buy."] },
        }),
      );
      expect(await screen.findByText("No candidate case.")).toBeInTheDocument();
    });

    it("does not send assertions that are not JSON, or empty, and shows the server's refusal", async () => {
      await open();
      fireEvent.change(screen.getByLabelText("Assertions"), { target: { value: "{nope" } });
      fireEvent.click(screen.getByRole("button", { name: "Add to the suite" }));
      expect(await screen.findByText("The assertions are not valid JSON.")).toBeInTheDocument();
      fireEvent.change(screen.getByLabelText("Assertions"), { target: { value: "{}" } });
      fireEvent.click(screen.getByRole("button", { name: "Add to the suite" }));
      expect(await screen.findByText(/Write at least one assertion/)).toBeInTheDocument();
      expect(mocks.acceptCandidate).not.toHaveBeenCalled();
      mocks.acceptCandidate.mockRejectedValue(new Error("not a need__operation of the assistant's tools"));
      fireEvent.change(screen.getByLabelText("Assertions"), { target: { value: '{"must_call": []}' } });
      fireEvent.click(screen.getByRole("button", { name: "Add to the suite" }));
      expect(await screen.findByText(/not a need__operation/)).toBeInTheDocument();
    });

    it("dismisses it", async () => {
      mocks.dismissCandidate.mockResolvedValue({ ...candidate, status: "dismissed" });
      await open();
      fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
      await waitFor(() => expect(mocks.dismissCandidate).toHaveBeenCalledWith("varga", "investment-analyst", "k1"));
    });
  });

  it("uninstalls, for an administrator only", async () => {
    mocks.uninstall.mockResolvedValue(undefined);
    const admin = wrap(<InstallationDetail slug="invest" />);
    fireEvent.click(await screen.findByRole("button", { name: "Uninstall" }));
    fireEvent.click(screen.getByRole("button", { name: "Uninstall" }));
    await waitFor(() => expect(mocks.uninstall).toHaveBeenCalledWith("varga", "invest"));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/assistants/manage"));
    admin.unmount();
    roles.admin = false;
    wrap(<InstallationDetail slug="invest" />);
    await screen.findByText("Settings");
    expect(screen.queryByRole("button", { name: "Uninstall" })).not.toBeInTheDocument();
  });
});

/**
 * The assistant's tokens, its stop and its resume (S64-09d, ADR 0141 §1, ADR 0142 §4 bis), against
 * mocked answers shaped by the contract (`AssistantStopResult`, `Assistant.stopped`).
 */
describe("one installation: its tokens, its stop and its resume", () => {
  it("narrows the tokens-and-cost panel to the installation, and links to its turns on the Activity screen", async () => {
    mocks.installation.mockResolvedValue(installed({ id: "inst-concierge" }));
    wrap(<InstallationDetail slug="invest" />);
    const panel = await screen.findByTestId("tokens-and-cost");
    await within(panel).findByTestId("panel-model");
    // Every read of the panel is narrowed to this installation, and its totals are the installation's alone.
    const reads = vi.mocked(api.orgCosts).mock.calls;
    expect(reads.length).toBeGreaterThan(0);
    expect(reads.every(([, , f]) => f?.assistant === "inst-concierge")).toBe(true);
    const own = mockCostReport("/orgs/varga/costs?group_by=model&assistant=inst-concierge");
    const all = mockCostReport("/orgs/varga/costs?group_by=assistant");
    expect(own.total.cost_usd).toBeCloseTo(all.rows.find((r) => r.key === "concierge")!.cost_usd);
    expect(own.total.cost_usd).toBeLessThan(all.total.cost_usd!);
    expect(within(panel).getByTestId("panel-model")).toHaveTextContent("claude-haiku");
    expect(within(panel).getByTestId("panel-model")).not.toHaveTextContent("claude-sonnet");
    expect(within(panel).getByRole("link", { name: "CSV" }).getAttribute("href")).toContain("assistant=inst-concierge");
    fireEvent.click(within(panel).getByRole("button", { name: "By agent" }));
    expect(await within(panel).findByTestId("panel-agent")).toHaveTextContent("concierge-agent");
    expect(screen.getByRole("link", { name: "Its conversation turns on the Activity screen" })).toHaveAttribute(
      "href",
      "/activity?assistant=inst-concierge",
    );
  });

  it("says the spend cannot be narrowed when the answer carries no installation id, and reads no report", async () => {
    wrap(<InstallationDetail slug="invest" />);
    expect(await screen.findByTestId("tokens-and-cost-unavailable")).toHaveTextContent("cannot be narrowed yet");
    expect(screen.queryByTestId("tokens-and-cost")).not.toBeInTheDocument();
    expect(api.orgCosts).not.toHaveBeenCalled();
  });

  it("stops with the reason typed, posts it, and shows the turns cancelled and what was not stopped", async () => {
    const stopped = { at: "2026-11-12T09:30:00Z", by: "lea@varga.dev", reason: "it quotes stale prices" };
    mocks.stop.mockImplementation(async () => {
      mocks.installation.mockResolvedValue(installed({ status: "suspended", stopped }));
      return {
        slug: "invest",
        status: "suspended",
        turns_cancelled: 2,
        not_stopped: [{ kind: "turn", id: "c-9", detail: "the replica did not answer" }],
      };
    });
    wrap(<InstallationDetail slug="invest" />);
    const card = await screen.findByTestId("assistant-stop");
    expect(within(card).queryByRole("button", { name: "Resume" })).not.toBeInTheDocument();
    fireEvent.click(within(card).getByRole("button", { name: "Stop the assistant…" }));
    const confirmation = within(card).getByTestId("confirmation");
    const confirm = within(confirmation).getByRole("button", { name: "Stop the assistant" });
    // No reason, no stop.
    expect(confirm).toBeDisabled();
    expect(within(confirmation).getByRole("textbox")).toHaveAttribute("maxLength", "500");
    fireEvent.change(within(confirmation).getByRole("textbox"), { target: { value: "  it quotes stale prices " } });
    await act(async () => {
      fireEvent.click(confirm);
    });
    expect(mocks.stop).toHaveBeenCalledWith("varga", "invest", "it quotes stale prices");
    const result = await screen.findByTestId("assistant-stop-result");
    expect(result).toHaveTextContent("Stopped. 2 conversation turns cancelled.");
    expect(result).toHaveTextContent("turn c-9: the replica did not answer");
    // The installation read again says who stopped it, when and why, and offers Resume.
    expect(await screen.findByTestId("assistant-stopped")).toHaveTextContent("Stopped by lea@varga.dev on");
    expect(screen.getByTestId("assistant-stopped")).toHaveTextContent("it quotes stale prices");
    expect(screen.getByRole("button", { name: "Resume" })).toBeInTheDocument();
  });

  it("keeps the confirmation open with the server's message when a stop is refused", async () => {
    mocks.stop.mockRejectedValue(new ApiError(403, { title: "Forbidden", detail: "stopping an assistant needs assistant:manage" }));
    wrap(<InstallationDetail slug="invest" />);
    const card = await screen.findByTestId("assistant-stop");
    fireEvent.click(within(card).getByRole("button", { name: "Stop the assistant…" }));
    fireEvent.change(within(card).getByRole("textbox"), { target: { value: "wrong answers" } });
    await act(async () => {
      fireEvent.click(within(card).getByRole("button", { name: "Stop the assistant" }));
    });
    expect(await within(card).findByRole("alert")).toHaveTextContent("stopping an assistant needs assistant:manage");
    expect(within(card).getByTestId("confirmation")).toBeInTheDocument();
    expect(screen.queryByTestId("assistant-stop-result")).not.toBeInTheDocument();
  });

  it("offers Resume only while the assistant is stopped, and resuming clears the notice", async () => {
    const stopped = { at: "2026-11-12T09:30:00Z", by: "lea@varga.dev", reason: "it quotes stale prices" };
    mocks.installation.mockResolvedValue(installed({ status: "suspended", stopped }));
    mocks.resume.mockImplementation(async () => {
      const ready = installed({ status: "ready", stopped: null });
      mocks.installation.mockResolvedValue(ready);
      return ready;
    });
    wrap(<InstallationDetail slug="invest" />);
    const notice = await screen.findByTestId("assistant-stopped");
    expect(notice).toHaveTextContent("Stopped by lea@varga.dev on");
    expect(screen.queryByRole("button", { name: "Stop the assistant…" })).not.toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    });
    expect(mocks.resume).toHaveBeenCalledWith("varga", "invest");
    await waitFor(() => expect(screen.queryByTestId("assistant-stopped")).not.toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Resume" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop the assistant…" })).toBeInTheDocument();
  });

  it("shows a refused resume", async () => {
    mocks.installation.mockResolvedValue(installed({ status: "suspended", stopped: { at: "2026-11-12T09:30:00Z", by: "lea", reason: "x" } }));
    mocks.resume.mockRejectedValue(new ApiError(409, { title: "Conflict", detail: "the assistant is not stopped" }));
    wrap(<InstallationDetail slug="invest" />);
    await act(async () => {
      fireEvent.click(await screen.findByRole("button", { name: "Resume" }));
    });
    expect(await screen.findByText("the assistant is not stopped")).toBeInTheDocument();
  });
});
