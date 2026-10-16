import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NetworkActivityCard } from "@/components/admin/network-activity";
import { api } from "@/lib/api";
import type { NetworkActivity } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, networkActivity: vi.fn() } };
});

function wrap(node: React.ReactNode) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{node}</QueryClientProvider>);
}

const NOW = new Date().toISOString();
const ROWS: NetworkActivity = {
  rows: [
    { host: "api.stripe.com", layer: "proxy", decision: "denied", reason: "not_allowed", count: 14, runs: 3, projects: ["billing-api"], agents: [], last_at: NOW },
    { host: "vendor.example.com", layer: "broker", decision: "rate_limited", reason: "per_host_per_minute:60", count: 6, runs: 2, projects: ["billing-api"], agents: [], last_at: NOW },
    { host: "github.com", layer: "proxy", decision: "allowed", reason: "allowed:github.com", count: 412, runs: 38, projects: ["billing-api", "checkout-web"], agents: [], last_at: NOW },
  ],
};

describe("network activity (ADR 0070)", () => {
  beforeEach(() => {
    vi.mocked(api.networkActivity).mockReset();
    vi.mocked(api.networkActivity).mockResolvedValue(ROWS);
  });

  it("shows the refusals first: host, where, decision, reason, counts", async () => {
    wrap(<NetworkActivityCard org="acme" />);
    const table = await screen.findByRole("table");
    expect(within(table).getByText("api.stripe.com")).toBeInTheDocument();
    expect(within(table).getByText("Egress proxy")).toBeInTheDocument();
    expect(within(table).getByText("Refused")).toBeInTheDocument();
    expect(within(table).getByText("not_allowed")).toBeInTheDocument();
    expect(within(table).getByText("Tool broker")).toBeInTheDocument();
    expect(within(table).getByText("Slowed down")).toBeInTheDocument();
    expect(within(table).queryByText("github.com"), "allowed traffic is hidden by default").toBeNull();
  });

  it("“Refused only” off lists the allowed traffic too", async () => {
    wrap(<NetworkActivityCard org="acme" />);
    await screen.findByRole("table");
    fireEvent.click(screen.getByLabelText("Refused only"));
    expect(await screen.findByText("github.com")).toBeInTheDocument();
    expect(screen.getByText("billing-api, checkout-web")).toBeInTheDocument();
  });

  it("asks the API for the chosen period", async () => {
    wrap(<NetworkActivityCard org="acme" />);
    await screen.findByRole("table");
    const first = vi.mocked(api.networkActivity).mock.calls[0]![1]!.since!;
    fireEvent.change(screen.getByLabelText("Period"), { target: { value: "7d" } });
    await waitFor(() => expect(api.networkActivity).toHaveBeenCalledTimes(2));
    const second = vi.mocked(api.networkActivity).mock.calls[1]![1]!.since!;
    expect(new Date(first).getTime() - new Date(second).getTime()).toBeGreaterThan(5 * 24 * 3_600_000);
  });

  it("says so when nothing was refused", async () => {
    vi.mocked(api.networkActivity).mockResolvedValue({ rows: [ROWS.rows[2]!] });
    wrap(<NetworkActivityCard org="acme" />);
    expect(await screen.findByText("Nothing was refused in this period.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("says when it cannot read", async () => {
    vi.mocked(api.networkActivity).mockImplementation(() => Promise.reject(new Error("forbidden")));
    wrap(<NetworkActivityCard org="acme" />);
    expect(await screen.findByText(/Could not read the network activity: forbidden/)).toBeInTheDocument();
  });
});
