import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { GatewayKeysCard } from "@/components/admin/gateway-keys";
import { api } from "@/lib/api";
import type { GatewayKeyInfo, GatewayKeyPage } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, gatewayKeys: vi.fn() } };
});

const keys: GatewayKeyInfo[] = [
  {
    key_id: "gk-1",
    run_id: "run-1",
    project_slug: "billing-api",
    budget_usd: 5,
    spend_usd: 1.5,
    expires_at: "2026-10-30T12:00:00Z",
    revoked: false,
    created_at: "2026-10-29T09:00:00Z",
  },
  {
    key_id: "gk-2",
    run_id: null,
    project_slug: "checkout-web",
    budget_usd: null,
    spend_usd: 0,
    expires_at: null,
    revoked: true,
    created_at: "2026-10-28T09:00:00Z",
  },
];

function page(
  items: GatewayKeyInfo[],
  next: string | null = null,
): GatewayKeyPage {
  return { items, meta: { has_more: next !== null, next_cursor: next } };
}

function show() {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <GatewayKeysCard />
    </QueryClientProvider>,
  );
}

describe("the gateway keys", () => {
  beforeEach(() => vi.mocked(api.gatewayKeys).mockReset());

  it("lists the keys and says when there are none", async () => {
    vi.mocked(api.gatewayKeys).mockResolvedValue(page(keys));
    show();
    expect(await screen.findByText("gk-1")).toBeInTheDocument();
    expect(screen.getByText("gk-2")).toBeInTheDocument();
    expect(screen.getByText("billing-api")).toBeInTheDocument();
    expect(screen.getAllByRole("row")).toHaveLength(3);
    expect(
      within(screen.getByRole("table")).getByText("Revoked"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "More" })).toBeNull();
  });

  it("shows the empty state for an empty list", async () => {
    vi.mocked(api.gatewayKeys).mockResolvedValue(page([]));
    show();
    expect(await screen.findByText("No gateway key")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("sends the filters to the server instead of filtering in the browser", async () => {
    vi.mocked(api.gatewayKeys).mockResolvedValue(page(keys));
    show();
    await screen.findByText("gk-1");
    expect(api.gatewayKeys).toHaveBeenLastCalledWith({
      q: undefined,
      state: undefined,
      cursor: undefined,
      limit: 50,
    });

    vi.mocked(api.gatewayKeys).mockResolvedValue(page([]));
    fireEvent.change(screen.getByLabelText("Filter keys"), {
      target: { value: " checkout " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Filter" }));
    await waitFor(() =>
      expect(api.gatewayKeys).toHaveBeenLastCalledWith({
        q: "checkout",
        state: undefined,
        cursor: undefined,
        limit: 50,
      }),
    );
    expect(
      await screen.findByText("No key matches this filter"),
    ).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Filter by state"), {
      target: { value: "revoked" },
    });
    await waitFor(() =>
      expect(api.gatewayKeys).toHaveBeenLastCalledWith({
        q: "checkout",
        state: "revoked",
        cursor: undefined,
        limit: 50,
      }),
    );
  });

  it("asks the next page by the cursor the server returned", async () => {
    vi.mocked(api.gatewayKeys)
      .mockResolvedValueOnce(page([keys[0] as GatewayKeyInfo], "cursor-1"))
      .mockResolvedValueOnce(page([keys[1] as GatewayKeyInfo]));
    show();
    await screen.findByText("gk-1");
    expect(screen.queryByText("gk-2")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    expect(await screen.findByText("gk-2")).toBeInTheDocument();
    expect(screen.getByText("gk-1")).toBeInTheDocument();
    expect(api.gatewayKeys).toHaveBeenLastCalledWith({
      q: undefined,
      state: undefined,
      cursor: "cursor-1",
      limit: 50,
    });
    expect(screen.queryByRole("button", { name: "More" })).toBeNull();
  });
});
