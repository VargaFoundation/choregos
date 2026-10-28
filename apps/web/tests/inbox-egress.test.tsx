import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { InboxView } from "@/components/inbox-view";
import { api } from "@/lib/api";
import { waitingInProject } from "@/lib/a-decider";
import type { EgressOverview, EgressRule } from "@/lib/types";

vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme" }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      projects: vi.fn().mockResolvedValue({ items: [], meta: { has_more: false } }),
      workItems: vi.fn().mockResolvedValue({ items: [], meta: { has_more: false } }),
      orgActions: vi.fn().mockResolvedValue([]),
      budgetAlerts: vi.fn().mockResolvedValue([]),
      orgEgress: vi.fn(),
      approveOrgEgressRequest: vi.fn().mockResolvedValue({}),
      refuseOrgEgressRequest: vi.fn().mockResolvedValue({}),
    },
  };
});

const request = (over: Partial<EgressRule> = {}): EgressRule => ({
  id: "eg-1",
  host: "mcp.example.test",
  ports: [443],
  used_by: ["api"],
  status: "requested",
  reason: "the vendor connector",
  origin: "org-connector:vendor",
  requested_by: "lea@acme.dev",
  requested_at: new Date().toISOString(),
  ...over,
});

const overview = (requests: EgressRule[], canDecide: boolean): EgressOverview => ({
  mode: "enforce",
  ceiling: {},
  rules: [],
  requests,
  can_decide: canDecide,
});

function view() {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <InboxView />
    </QueryClientProvider>,
  );
}

beforeEach(() => vi.clearAllMocks());

describe("the inbox's network access card (S39-22)", () => {
  it("lists the request with its host, family, reason, origin link and who asked", async () => {
    vi.mocked(api.orgEgress).mockResolvedValue(overview([request()], true));
    view();
    const card = await screen.findByTestId("egress-eg-1");
    expect(within(card).getByText("mcp.example.test")).toBeInTheDocument();
    expect(card.textContent).toContain("the API");
    expect(card.textContent).toContain("the vendor connector");
    expect(card.textContent).toContain("asked by lea@acme.dev");
    expect(within(card).getByRole("link", { name: "connector vendor" })).toHaveAttribute("href", "/admin/connectors?connector=vendor");
  });

  it("an administrator approves it: the endpoint is called and the list is read again", async () => {
    vi.mocked(api.orgEgress).mockResolvedValueOnce(overview([request()], true)).mockResolvedValue(overview([], true));
    view();
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    await waitFor(() => expect(api.approveOrgEgressRequest).toHaveBeenCalledWith("acme", "eg-1"));
    await waitFor(() => expect(screen.queryByTestId("egress-eg-1")).toBeNull());
    expect(screen.getByText("No request waits")).toBeInTheDocument();
  });

  it("an administrator can refuse", async () => {
    vi.mocked(api.orgEgress).mockResolvedValue(overview([request()], true));
    view();
    fireEvent.click(await screen.findByRole("button", { name: "Refuse" }));
    await waitFor(() => expect(api.refuseOrgEgressRequest).toHaveBeenCalledWith("acme", "eg-1"));
  });

  it("anyone else reads it, and is told an administrator decides", async () => {
    vi.mocked(api.orgEgress).mockResolvedValue(overview([request()], false));
    view();
    const card = await screen.findByTestId("egress-eg-1");
    expect(within(card).queryByRole("button", { name: "Approve" })).toBeNull();
    expect(card.textContent).toContain("an administrator decides");
  });

  it("is an organisation card: a project's inbox does not read it", () => {
    expect(waitingInProject([], [], "billing-api")).toEqual({ actions: [], budgets: [] });
    render(
      <QueryClientProvider client={new QueryClient()}>
        <InboxView project="billing-api" />
      </QueryClientProvider>,
    );
    expect(api.orgEgress).not.toHaveBeenCalled();
    expect(screen.queryByText("Network access")).toBeNull();
  });
});
