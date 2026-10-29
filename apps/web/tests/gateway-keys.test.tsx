import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { GatewayKeysCard } from "@/components/admin/gateway-keys";
import { api } from "@/lib/api";
import type { GatewayKeyInfo } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, gatewayKeys: vi.fn() } };
});

const keys: GatewayKeyInfo[] = [
  { key_id: "gk-1", run_id: "run-1", project_slug: "billing-api", budget_usd: 5, spend_usd: 1.5, expires_at: "2026-10-30T12:00:00Z", revoked: false, created_at: "2026-10-29T09:00:00Z" },
  { key_id: "gk-2", run_id: null, project_slug: "checkout-web", budget_usd: null, spend_usd: 0, expires_at: null, revoked: true, created_at: "2026-10-28T09:00:00Z" },
];

function show() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <GatewayKeysCard />
    </QueryClientProvider>,
  );
}

describe("the gateway keys", () => {
  beforeEach(() => vi.mocked(api.gatewayKeys).mockReset());

  it("lists the keys and says when there are none", async () => {
    vi.mocked(api.gatewayKeys).mockResolvedValue(keys);
    show();
    expect(await screen.findByText("gk-1")).toBeInTheDocument();
    expect(screen.getByText("gk-2")).toBeInTheDocument();
    expect(screen.getByText("billing-api")).toBeInTheDocument();
    expect(screen.getByText("checkout-web")).toBeInTheDocument();
    expect(screen.getAllByRole("row")).toHaveLength(3);
    expect(screen.getByText("Revoked")).toBeInTheDocument();
  });

  it("shows the empty state for an empty list", async () => {
    vi.mocked(api.gatewayKeys).mockResolvedValue([]);
    show();
    expect(await screen.findByText("No gateway key")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });
});
