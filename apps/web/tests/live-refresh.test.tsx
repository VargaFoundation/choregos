import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { keyNames, LIVE_DEBOUNCE_MS, useLiveRefresh } from "@/lib/live";

/** S59-08: a ticket's page refetched only on reload; it now follows its events. */
class FakeSource {
  static last: FakeSource | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  closed = false;
  constructor(public url: string) {
    FakeSource.last = this;
  }
  close() {
    this.closed = true;
  }
}

function Page({ id }: { id: string }) {
  useLiveRefresh(`/work-items/${id}/events/stream`, keyNames(id));
  return null;
}

describe("a live page", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal("EventSource", FakeSource);
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("refetches its work item's queries once per burst of events, and only them", async () => {
    const client = new QueryClient();
    const spy = vi.spyOn(client, "invalidateQueries");
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { unmount } = render(<Page id="w-1" />, { wrapper });
    const source = FakeSource.last!;
    expect(source.url).toBe("/api/v1/work-items/w-1/events/stream");

    act(() => {
      for (let i = 0; i < 3; i += 1) source.onmessage?.(new MessageEvent("message", { data: "{}" }));
      vi.advanceTimersByTime(LIVE_DEBOUNCE_MS + 1);
    });
    expect(spy).toHaveBeenCalledTimes(1);
    const predicate = spy.mock.calls[0]![0]!.predicate!;
    expect(predicate({ queryKey: ["journey", "w-1"] } as never)).toBe(true);
    expect(predicate({ queryKey: ["journey", "w-2"] } as never)).toBe(false);

    unmount();
    expect(source.closed).toBe(true);
  });
});
