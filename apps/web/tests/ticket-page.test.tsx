import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { Suspense } from "react";
import { describe, expect, it, vi } from "vitest";
import WorkItemPage from "@/app/p/[slug]/items/[id]/page";
import { api } from "@/lib/api";
import type { WorkflowDef, WorkItemDto } from "@/lib/types";
import { journeyInProgress } from "@/mocks/journey";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/p/billing-api/items/w1",
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
      workflowNamed: vi.fn(),
    },
  };
});
vi.mock("@/lib/sse", () => ({ useEventStream: () => ({ events: [], connected: false, error: null }) }));

// React reads `status`/`value` on an already-settled promise and renders at once, without suspending.
const params = Object.assign(Promise.resolve({ slug: "billing-api", id: "w1" }), {
  status: "fulfilled",
  value: { slug: "billing-api", id: "w1" },
});

const item = {
  id: "w1",
  project_slug: "billing-api",
  tracker_key: "OPS-7",
  title: "Database down in eu-west",
  state: "triage",
  workflow_name: "hotfix",
  workflow_version: 2,
  created_at: "2026-11-03T10:00:00Z",
  totals: {},
  fields: { incident_id: "INC-42", severity: "sev1" },
} as unknown as WorkItemDto;

const hotfix = {
  name: "hotfix",
  version: 2,
  yaml: "",
  json: {
    metadata: {
      name: "hotfix",
      inputs: {
        type: "object",
        properties: { incident_id: { type: "string", title: "incident number" }, severity: { type: "string" } },
      },
    },
  },
} as unknown as WorkflowDef;

function ticket() {
  vi.mocked(api.workItem).mockResolvedValue(item);
  vi.mocked(api.journey).mockResolvedValue(journeyInProgress(Date.parse("2026-11-04T12:00:00Z")));
  vi.mocked(api.workflowNamed).mockResolvedValue(hotfix);
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <Suspense fallback={null}>
        <WorkItemPage params={params} />
      </Suspense>
    </QueryClientProvider>,
  );
}

describe("a ticket's page, finished (S55-14)", () => {
  it("labels the request's fields with the titles its workflow declares", async () => {
    ticket();
    const fields = await screen.findByLabelText("Fields of the request");
    expect(await within(fields).findByText("Incident number")).toBeInTheDocument();
    expect(fields).not.toHaveTextContent("Incident id");
    // A field the workflow gives no title keeps its name, humanised.
    expect(within(fields).getByText("Severity")).toBeInTheDocument();
    expect(api.workflowNamed).toHaveBeenCalledWith("billing-api", "hotfix");
  });

  it("keeps the replay player folded in the journey card", async () => {
    ticket();
    const player = await screen.findByRole("toolbar", { name: "Replay the journey" });
    const fold = player.closest("details");
    expect(fold).not.toBeNull();
    expect(fold).not.toHaveAttribute("open");
    expect(within(fold!).getByText("Replay this ticket")).toBeInTheDocument();
  });

  it("shows a ticket's size and risk in its facts, in words, whatever its workflow declares (ADR 0129)", async () => {
    vi.mocked(api.workItem).mockResolvedValue({ ...item, size: "M", risk: "high" });
    vi.mocked(api.journey).mockResolvedValue(journeyInProgress(Date.parse("2026-11-04T12:00:00Z")));
    vi.mocked(api.workflowNamed).mockResolvedValue(hotfix);
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <Suspense fallback={null}>
          <WorkItemPage params={params} />
        </Suspense>
      </QueryClientProvider>,
    );
    const facts = await screen.findByTestId("ticket-facts");
    const risk = within(facts).getByTestId("ticket-risk");
    expect(risk).toHaveTextContent("High");
    expect(risk.className).toContain("failed");
    const size = within(facts).getByTestId("ticket-size");
    expect(size).toHaveTextContent("M");
    expect(size).toHaveAccessibleName("M, 2 of 4");
    expect(size.className).not.toContain("failed");
  });

  it("names no size or risk a ticket does not have", async () => {
    ticket();
    const facts = await screen.findByTestId("ticket-facts");
    expect(within(facts).queryByTestId("ticket-size")).toBeNull();
    expect(within(facts).queryByText("Risk")).toBeNull();
  });
});
