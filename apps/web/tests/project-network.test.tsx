import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProjectNetworkCard } from "@/components/project/network-card";
import { api } from "@/lib/api";
import type { ProjectNetwork } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, projectNetwork: vi.fn() } };
});

function wrap(node: React.ReactNode) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{node}</QueryClientProvider>);
}

const NETWORK: ProjectNetwork = {
  policy: { allow_domains: ["pypi.org", "api.stripe.com"] },
  ceiling: { allow_domains: ["pypi.org", "github.com"], deny_domains: ["gist.github.com"] },
  effective: {
    allow: ["pypi.org"],
    deny: ["gist.github.com"],
    implied: ["github.com"],
    per_host_per_minute: 60,
    per_run_per_minute: null,
    org_per_host_per_minute: 60,
    builtin_web_tools: false,
  },
  clamps: ["network.allow_domains: `api.stripe.com` is outside the organisation's allowed hosts"],
};

describe("the project's effective network (ADR 0070)", () => {
  beforeEach(() => {
    vi.mocked(api.projectNetwork).mockReset();
  });

  it("lists the allowed hosts, marks the repository host, the denied ones, the rates and the clamps", async () => {
    vi.mocked(api.projectNetwork).mockResolvedValue(NETWORK);
    wrap(<ProjectNetworkCard slug="billing-api" />);
    await screen.findByText("pypi.org");
    const card = screen.getByTestId("project-network");
    expect(within(card).getByText(/· repository/).parentElement).toHaveTextContent("github.com");
    expect(within(card).getByText("gist.github.com")).toBeInTheDocument();
    expect(within(card).getByText("Requests per minute, per host").nextSibling).toHaveTextContent("60");
    expect(within(card).getByText("Requests per minute, per run").nextSibling).toHaveTextContent("no cap");
    expect(within(card).getByText("Built-in web tools").nextSibling).toHaveTextContent("Off");
    expect(within(card).getByTestId("network-clamps")).toHaveTextContent("api.stripe.com");
    expect(within(card).getByText(/Edit/)).toHaveTextContent("sandbox.network");
  });

  it("says no internet when nothing is allowed", async () => {
    vi.mocked(api.projectNetwork).mockResolvedValue({
      ...NETWORK,
      clamps: [],
      effective: { ...NETWORK.effective, allow: [], implied: [], deny: [], builtin_web_tools: true },
    });
    wrap(<ProjectNetworkCard slug="billing-api" />);
    expect(await screen.findByText(/No internet: a run reaches no host outside the platform/)).toBeInTheDocument();
    expect(screen.queryByTestId("network-clamps")).toBeNull();
  });

  it("says when it cannot read", async () => {
    vi.mocked(api.projectNetwork).mockImplementation(async () => {
      throw new Error("boom");
    });
    wrap(<ProjectNetworkCard slug="billing-api" />);
    expect(await screen.findByText(/Could not read the network access: boom/)).toBeInTheDocument();
  });
});
