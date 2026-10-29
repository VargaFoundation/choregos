import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ReceivedEvents } from "@/components/project-settings/received-events";
import { api } from "@/lib/api";
import type { ReceivedEvent, ReceivedEventPage } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, receivedEvents: vi.fn() } };
});

const event = (id: string, outcome: string, extra: Partial<ReceivedEvent> = {}): ReceivedEvent => ({
  id,
  received_at: "2026-10-28T20:00:00Z",
  source: "jira",
  event_type: "tracker.item.labeled",
  outcome: outcome as ReceivedEvent["outcome"],
  work_item_key: "XXX-7",
  work_item_id: "wi-1",
  summary: "tracker.item.labeled · XXX-7 · label added: agent-ready",
  ...extra,
});

const page = (items: ReceivedEvent[], next: string | null): ReceivedEventPage => ({
  items,
  meta: { has_more: next !== null, next_cursor: next },
});

const view = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ReceivedEvents slug="yyy" />
    </QueryClientProvider>,
  );

describe("a project's received webhook events (S42-06f)", () => {
  it("lists each event with what Choregos did, its work item and its summary, and pages back", async () => {
    vi.mocked(api.receivedEvents).mockImplementation(async (_slug, params) =>
      params?.cursor === "e-2"
        ? page([event("e-3", "ignored_not_agent_ready", { work_item_key: "XXX-9", work_item_id: null })], null)
        : page([event("e-1", "created_work_item"), event("e-2", "signalled", { event_type: "tracker.item.moved" })], "e-2"),
    );
    view();

    const first = await screen.findByTestId("received-e-1");
    expect(first).toHaveTextContent("started a new work item");
    expect(first).toHaveTextContent("label added: agent-ready");
    expect(within(first).getByRole("link", { name: "XXX-7" })).toHaveAttribute("href", "/p/yyy/items/wi-1");
    expect(screen.getByTestId("received-e-2")).toHaveTextContent("passed to its workflow");

    fireEvent.click(screen.getByRole("button", { name: "Older events" }));
    const older = await screen.findByTestId("received-e-3");
    expect(older).toHaveTextContent("ignored: not labelled agent-ready");
    expect(within(older).queryByRole("link")).toBeNull();
    expect(vi.mocked(api.receivedEvents)).toHaveBeenLastCalledWith("yyy", { cursor: "e-2", limit: "20" });
    expect(screen.queryByRole("button", { name: "Older events" })).toBeNull();
  });

  it("says when nothing has been received, and where to look", async () => {
    vi.mocked(api.receivedEvents).mockResolvedValue(page([], null));
    view();
    expect(await screen.findByTestId("received-events-none")).toHaveTextContent("check its webhook");
  });
});
