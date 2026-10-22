import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProjectNetworkCard } from "@/components/project/network-card";
import { api } from "@/lib/api";
import { readSandboxNetwork } from "@/lib/policy-network";
import type { PolicyDef, ProjectNetwork } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, projectNetwork: vi.fn(), policy: vi.fn(), putPolicy: vi.fn() } };
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

const POLICY = "# the team's policy\nbudgets:\n  per_ticket_usd: 25\nsandbox:\n  network:\n    allow_domains: [pypi.org, api.stripe.com]\n";

function policyOf(yaml: string): PolicyDef {
  return { name: "default", version: 1, yaml, is_active: true };
}

describe("the project's effective network (ADR 0070)", () => {
  beforeEach(() => {
    vi.mocked(api.projectNetwork).mockReset();
    vi.mocked(api.policy).mockReset();
    vi.mocked(api.putPolicy).mockReset();
    vi.mocked(api.policy).mockResolvedValue(policyOf(POLICY));
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

describe("the project's network, edited in its settings (S41-22)", () => {
  beforeEach(() => {
    vi.mocked(api.projectNetwork).mockReset().mockResolvedValue({ ...NETWORK, clamps: [] });
    vi.mocked(api.policy).mockReset().mockResolvedValue(policyOf(POLICY));
    vi.mocked(api.putPolicy).mockReset().mockResolvedValue(policyOf(POLICY));
  });

  async function editor() {
    wrap(<ProjectNetworkCard slug="billing-api" />);
    const form = await screen.findByTestId("project-network-editor");
    await waitFor(() => expect(within(form).getByRole("button", { name: "Save the network access" })).toBeEnabled());
    return form;
  }

  it("starts from the stored network and saves the edit into the policy, every other line kept", async () => {
    const form = await editor();
    const allowed = within(form).getByLabelText(/Allowed hosts/);
    expect(allowed).toHaveValue("pypi.org\napi.stripe.com");
    fireEvent.change(allowed, { target: { value: "pypi.org\n*.githubusercontent.com" } });
    fireEvent.change(within(form).getByLabelText(/Per-host cap/), { target: { value: "30" } });
    fireEvent.click(within(form).getByRole("checkbox"));
    fireEvent.click(within(form).getByRole("button", { name: "Save the network access" }));
    expect(await within(form).findByText("Network access saved")).toBeInTheDocument();
    expect(api.putPolicy).toHaveBeenCalledTimes(1);
    const [slug, yaml] = vi.mocked(api.putPolicy).mock.calls[0]!;
    expect(slug).toBe("billing-api");
    expect(yaml).toContain("# the team's policy\nbudgets:\n  per_ticket_usd: 25\n");
    expect(readSandboxNetwork(yaml)).toEqual({
      allow_domains: ["pypi.org", "*.githubusercontent.com"],
      deny_domains: [],
      per_host_per_minute: 30,
      builtin_web_tools: "deny",
    });
  });

  it("refuses a host that is not one, before anything is sent", async () => {
    const form = await editor();
    fireEvent.change(within(form).getByLabelText(/Allowed hosts/), { target: { value: "https://pypi.org/simple" } });
    expect(within(form).getByTestId("project-network-problems")).toBeInTheDocument();
    expect(within(form).getByRole("button", { name: "Save the network access" })).toBeDisabled();
    fireEvent.change(within(form).getByLabelText(/Per-run cap/), { target: { value: "0" } });
    expect(within(form).getByTestId("project-network-problems")).toHaveTextContent("a whole number from 1");
  });

  it("says what the organisation's ceiling will refuse", async () => {
    vi.mocked(api.projectNetwork).mockResolvedValue({
      ...NETWORK,
      clamps: [],
      ceiling: { ...NETWORK.ceiling, per_host_per_minute: 60, builtin_web_tools: "deny" },
    });
    const form = await editor();
    const flagged = within(form).getByTestId("project-network-flagged");
    expect(flagged).toHaveTextContent("api.stripe.com");
    expect(flagged).toHaveTextContent("Per-host cap (requests per minute): the organisation caps it at 60");
    expect(flagged).toHaveTextContent("Built-in web tools: the organisation switches them off");
  });

  it("refuses to rewrite a policy it cannot read, and sends nothing", async () => {
    vi.mocked(api.policy).mockResolvedValue(policyOf("budgets: [\n"));
    const form = await editor();
    fireEvent.click(within(form).getByRole("button", { name: "Save the network access" }));
    expect(await within(form).findByText(/does not parse/)).toBeInTheDocument();
    expect(api.putPolicy).not.toHaveBeenCalled();
  });
});
