import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { InboxView } from "@/components/inbox-view";

vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme" }) }));
const workItems = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      projects: vi.fn().mockResolvedValue({
        items: [{ slug: "billing-api" }, { slug: "checkout-web" }],
        meta: { has_more: false },
      }),
      workItems,
      orgActions: vi.fn().mockResolvedValue([
        { id: "a1", title: "Open a billing account", project_slug: "billing-api", status: "pending_approval", proposed_by: "agent:x" },
        { id: "a2", title: "Order a checkout laptop", project_slug: "checkout-web", status: "pending_approval", proposed_by: "agent:x" },
      ]),
      budgetAlerts: vi.fn().mockResolvedValue([]),
    },
  };
});

function view(project?: string) {
  workItems.mockResolvedValue({ items: [], meta: { has_more: false } });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <InboxView project={project} heading="h2" />
    </QueryClientProvider>,
  );
}

describe("the project's inbox (S39-04)", () => {
  it("lists the project's action and not another project's, and links to the organisation's inbox", async () => {
    view("billing-api");
    expect(await screen.findByText("Open a billing account")).toBeInTheDocument();
    expect(screen.queryByText("Order a checkout laptop")).toBeNull();
    expect(screen.getByRole("link", { name: /whole organisation/ })).toHaveAttribute("href", "/inbox");
  });

  it("reads the tickets of that project only", async () => {
    workItems.mockClear();
    view("billing-api");
    await screen.findByText("Open a billing account");
    expect(workItems.mock.calls.map((c) => c[0])).toEqual(["billing-api"]);
  });

  it("without a project, lists everything and has no link to itself", async () => {
    view();
    expect(await screen.findByText("Order a checkout laptop")).toBeInTheDocument();
    expect(screen.getByText("Open a billing account")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /whole organisation/ })).toBeNull();
  });
});
