import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { Suspense, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import WorkItemPage from "@/app/p/[slug]/items/[id]/page";
import { DecisionBar } from "@/components/decision-bar";
import {
  DecisionDraftCard,
  draftedVia,
} from "@/components/ticket/decision-draft";
import { api, ApiError } from "@/lib/api";
import type { DecisionDraft, HumanRequest, WorkItemDto } from "@/lib/types";
import { journeyInProgress } from "@/mocks/journey";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/p/fon/items/w1",
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      workItem: vi.fn(),
      timeline: vi.fn().mockResolvedValue([]),
      handOffs: vi.fn().mockResolvedValue([]),
      runs: vi.fn().mockResolvedValue([]),
      connectors: vi.fn().mockResolvedValue([]),
      artifacts: vi.fn().mockResolvedValue([]),
      journey: vi.fn(),
      workflowNamed: vi
        .fn()
        .mockResolvedValue({
          name: "release",
          version: 1,
          yaml: "",
          json: { metadata: {} },
        }),
      workflows: vi
        .fn()
        .mockResolvedValue([
          { name: "release", version: 1, is_default: true, open_items: 1 },
        ]),
      me: vi.fn(),
      project: vi
        .fn()
        .mockResolvedValue({ id: "p1", slug: "fon", org: "acme", name: "fon" }),
      members: vi.fn().mockResolvedValue([
        {
          user_id: "u2",
          email: "max@acme.dev",
          org: "acme",
          role: "developer",
        },
        {
          user_id: "u2",
          email: "max@acme.dev",
          org: "acme",
          project_slug: "fon",
          role: "project_owner",
        },
        {
          user_id: "u1",
          email: "lea@acme.dev",
          org: "acme",
          role: "developer",
        },
      ]),
      reassign: vi.fn(),
      discardDraft: vi.fn(),
      decide: vi.fn(),
    },
  };
});
vi.mock("@/lib/sse", () => ({
  useEventStream: () => ({ events: [], connected: false, error: null }),
}));

const developer = {
  id: "u1",
  email: "lea@acme.dev",
  memberships: [{ org: "acme", role: "developer" }],
};

const draft: DecisionDraft = {
  kind: "reject",
  reason: "the changelog is missing",
  note: "the release notes do not mention the migration",
  summary: "reject: the changelog is missing",
  by: "lea@acme.dev",
  via: {
    door: "mcp",
    client: "claude-code",
    agent: "lea-claude",
    user_agent: "claude-code/2.1",
  },
  at: "2026-11-14T08:00:00Z",
};

const waiting: HumanRequest = {
  id: "r1",
  kind: "approval",
  requested_at: "2026-11-14T07:00:00Z",
  payload: { summary: "Ship?" },
  assignee: { user: "lea@acme.dev", group: null, reassigned: false },
  draft,
};

function withClient(children: ReactNode) {
  return (
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      {children}
    </QueryClientProvider>
  );
}

describe("a client's draft on the awaited decision (ADR 0163)", () => {
  beforeEach(() => {
    vi.mocked(api.discardDraft).mockReset();
    vi.mocked(api.decide).mockReset();
  });

  it("says who prepared it and through what", () => {
    expect(draftedVia({ door: "mcp", client: "claude-code" })).toBe(
      "the client claude-code",
    );
    expect(
      draftedVia({ door: "mcp", client: "cursor", agent: "lea-cursor" }),
    ).toBe("the client cursor (external agent lea-cursor)");
    expect(draftedVia({ door: "mcp" })).toBe("a client");
  });

  it("shows the proposal, its author and its note, and discards it without deciding", async () => {
    vi.mocked(api.discardDraft).mockResolvedValue(undefined);
    const onTake = vi.fn();
    const onDone = vi.fn();
    render(
      withClient(
        <DecisionDraftCard
          itemId="w1"
          requestId="r1"
          draft={draft}
          onTake={onTake}
          onDone={onDone}
        />,
      ),
    );
    const card = screen.getByTestId("decision-draft");
    expect(card).toHaveTextContent("proposes to send back");
    expect(card).toHaveTextContent(
      "prepared by the client claude-code (external agent lea-claude) for lea@acme.dev",
    );
    expect(screen.getByTestId("decision-draft-note")).toHaveTextContent(
      "do not mention the migration",
    );
    await act(async () =>
      fireEvent.click(screen.getByRole("button", { name: "Discard" })),
    );
    expect(api.discardDraft).toHaveBeenCalledWith("w1", "r1");
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(onTake).not.toHaveBeenCalled();
    expect(api.decide).not.toHaveBeenCalled();
  });

  it("shows the API's refusal to discard", async () => {
    vi.mocked(api.discardDraft).mockRejectedValue(
      new ApiError(409, { detail: "this request has already been decided" }),
    );
    render(
      withClient(
        <DecisionDraftCard
          itemId="w1"
          requestId="r1"
          draft={draft}
          onTake={vi.fn()}
          onDone={vi.fn()}
        />,
      ),
    );
    await act(async () =>
      fireEvent.click(screen.getByRole("button", { name: "Discard" })),
    );
    expect(await screen.findByText(/already been decided/)).toBeInTheDocument();
  });

  it("fills a task's form when taken, and never attests for the person", async () => {
    vi.mocked(api.decide).mockResolvedValue(undefined);
    const task = {
      kind: "complete" as const,
      values: { comment: "paid" },
      summary: "complete",
      by: "lea@acme.dev",
      via: { door: "mcp" as const },
      at: draft.at,
    };
    render(
      withClient(
        <DecisionBar
          itemId="w1"
          kind="task"
          request={{
            payload: {
              form: {
                type: "object",
                properties: { comment: { type: "string" } },
                required: ["comment"],
              },
              attest: "I checked it",
            },
          }}
          taken={task}
        />,
      ),
    );
    expect(screen.getByTestId("draft-taken")).toHaveTextContent(
      "nothing is sent until you press a button",
    );
    expect(screen.getByDisplayValue("paid")).toBeInTheDocument();
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Done" })).toBeDisabled();
    expect(api.decide).not.toHaveBeenCalled();
    await act(async () => fireEvent.click(screen.getByRole("checkbox")));
    await act(async () =>
      fireEvent.click(screen.getByRole("button", { name: "Done" })),
    );
    expect(api.decide).toHaveBeenCalledWith("w1", {
      kind: "complete",
      values: { comment: "paid" },
      attested: true,
    });
  });

  it("is shown on the ticket's awaited decision, and Take fills the form without sending it", async () => {
    const params = Object.assign(Promise.resolve({ slug: "fon", id: "w1" }), {
      status: "fulfilled",
      value: { slug: "fon", id: "w1" },
    });
    vi.mocked(api.workItem).mockResolvedValue({
      id: "w1",
      project_slug: "fon",
      tracker_key: "FON-12",
      title: "Ship 1.2",
      state: "waiting",
      workflow_name: "release",
      workflow_version: 1,
      created_at: "2026-11-14T07:00:00Z",
      totals: {},
      pending_request: waiting,
    } as unknown as WorkItemDto);
    vi.mocked(api.journey).mockResolvedValue(
      journeyInProgress(Date.parse("2026-11-14T09:00:00Z")),
    );
    vi.mocked(api.me).mockResolvedValue(developer as never);
    render(
      withClient(
        <Suspense fallback={null}>
          <WorkItemPage params={params} />
        </Suspense>,
      ),
    );
    expect(await screen.findByTestId("decision-draft")).toHaveTextContent(
      "reject: the changelog is missing",
    );
    const reason = screen.getByPlaceholderText(
      "Reason (if sent back)",
    ) as HTMLInputElement;
    expect(reason.value).toBe("");
    await act(async () =>
      fireEvent.click(screen.getByRole("button", { name: "Take" })),
    );
    expect(
      (screen.getByPlaceholderText("Reason (if sent back)") as HTMLInputElement)
        .value,
    ).toBe("the changelog is missing");
    expect(screen.getByTestId("draft-taken")).toBeInTheDocument();
    expect(api.decide).not.toHaveBeenCalled();
  });
});
