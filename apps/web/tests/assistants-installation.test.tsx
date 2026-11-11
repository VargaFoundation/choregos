import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { InstallAssistant, groupsOf } from "@/components/assistants/install-assistant";
import { InstallationDetail } from "@/components/assistants/installation-detail";
import { InstallationsList } from "@/components/assistants/installations-list";
import { api } from "@/lib/api";

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
      agents: vi.fn(),
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
  knowledgeUpdates: vi.fn(),
  decideKnowledgeUpdate: vi.fn(),
  reads: vi.fn(),
  eraseSubject: vi.fn(),
  feedbackCandidates: vi.fn(),
  webhookSecret: vi.fn(),
  acceptCandidate: vi.fn(),
  dismissCandidate: vi.fn(),
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
