import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, within } from "@testing-library/react";
import { Suspense, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ProjectOverview from "@/app/p/[slug]/page";
import { CrossBackendCard } from "@/components/cross-backend-card";
import { api } from "@/lib/api";
import type { CrossBackendReport } from "@/lib/types";

/** The cross-backend review on the project's overview (S47-17): the API's report, read by a person. */
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), usePathname: () => "/p/billing-api" }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme", me: null }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  const down = () => Promise.reject(new Error("503 Service Unavailable"));
  return {
    ...real,
    api: {
      ...real.api,
      crossBackend: vi.fn(),
      project: vi.fn(() => Promise.resolve({ slug: "billing-api", status: "active", stats: {} })),
      costs: vi.fn(down),
      dora: vi.fn(down),
      workItems: vi.fn(down),
      projectRequirements: vi.fn(down),
      connectors: vi.fn(down),
      workflows: vi.fn(down),
    },
  };
});

const REPORT: CrossBackendReport = {
  since: "2026-09-28T00:00:00Z",
  cross_backend_required: true,
  same_backend: { reviews: 10, caught: 2, catch_rate: 0.2, backends: ["claude-code"] },
  other_backend: { reviews: 5, caught: 2, catch_rate: 0.4, backends: ["codex", "gemini-cli"] },
  verdict: "cross review catches more",
  detail: "another backend finds something in 40% of the reviews, the same one in 20% (+20.0%).",
};

async function show(node: ReactNode) {
  await act(async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <Suspense fallback="…">{node}</Suspense>
      </QueryClientProvider>,
    );
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  document.body.innerHTML = "";
});

describe("the cross-backend review card", () => {
  it("shows the verdict and both arms", async () => {
    vi.mocked(api.crossBackend).mockResolvedValue(REPORT);
    await show(<CrossBackendCard slug="billing-api" />);
    expect(await screen.findByText("Cross review catches more")).toBeInTheDocument();
    expect(screen.getByText("Another backend finds something in 40% of the reviews, the same one in 20% (+20.0%).")).toBeInTheDocument();
    expect(screen.getByText("required by the policy")).toBeInTheDocument();
    const other = screen.getByRole("group", { name: "Reviewed by another backend" });
    expect(within(other).getByText("40 %")).toBeInTheDocument();
    expect(within(other).getByText("2 caught out of 5 reviews")).toBeInTheDocument();
    expect(within(other).getByText("codex, gemini-cli")).toBeInTheDocument();
    const same = screen.getByRole("group", { name: "Reviewed by the same backend" });
    expect(within(same).getByText("20 %")).toBeInTheDocument();
    expect(within(same).getByText("2 caught out of 10 reviews")).toBeInTheDocument();
    expect(within(same).getByText("claude-code")).toBeInTheDocument();
    expect(api.crossBackend).toHaveBeenCalledWith("billing-api");
  });

  it("says no rate and no badge when nothing was measured and the policy does not require it", async () => {
    vi.mocked(api.crossBackend).mockResolvedValue({
      since: "2026-09-28T00:00:00Z",
      cross_backend_required: false,
      same_backend: { reviews: 0, caught: 0, backends: [] },
      other_backend: { reviews: 0, caught: 0, backends: [] },
      verdict: "not enough data",
      detail: "",
    });
    await show(<CrossBackendCard slug="billing-api" />);
    expect(await screen.findByText("Not enough data")).toBeInTheDocument();
    expect(screen.queryByText("required by the policy")).toBeNull();
    expect(screen.getAllByText("—")).toHaveLength(2);
    expect(screen.getAllByText("No review yet")).toHaveLength(2);
  });

  it("says a failed read instead of an empty card", async () => {
    vi.mocked(api.crossBackend).mockRejectedValue(new Error("503 Service Unavailable"));
    await show(<CrossBackendCard slug="billing-api" />);
    expect(await screen.findByText(/Could not read the cross-backend review: 503 Service Unavailable/)).toBeInTheDocument();
  });

  it("sits on the project's overview, after DORA", async () => {
    vi.mocked(api.crossBackend).mockResolvedValue(REPORT);
    await show(<ProjectOverview params={Promise.resolve({ slug: "billing-api" })} />);
    const card = (await screen.findByText("Cross review catches more")).closest("section");
    expect(card).not.toBeNull();
    const dora = screen.getByRole("heading", { name: "DORA" }).closest("section");
    expect(dora!.compareDocumentPosition(card!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(api.crossBackend).toHaveBeenCalledWith("billing-api");
  });
});
