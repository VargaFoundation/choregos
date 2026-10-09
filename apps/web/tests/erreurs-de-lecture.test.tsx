import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { Suspense, type ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import PlatformPage from "@/app/admin/platform/page";
import FindingsPage from "@/app/p/[slug]/findings/page";
import MemoryPage from "@/app/p/[slug]/memory/page";
import ProjectOverview from "@/app/p/[slug]/page";
import TrainsPage from "@/app/p/[slug]/trains/page";
import { api } from "@/lib/api";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), usePathname: () => "/" }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "varga", me: null }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  const panne = () => Promise.reject(new Error("503 Service Unavailable"));
  return {
    ...reel,
    api: {
      ...reel.api,
      project: vi.fn(() => Promise.resolve({ slug: "billing-api", status: "active", stats: {} })),
      costs: vi.fn(panne),
      dora: vi.fn(panne),
      workItems: vi.fn(panne),
      projectRequirements: vi.fn(panne),
      connectors: vi.fn(panne),
      workflows: vi.fn(panne),
      train: vi.fn(panne),
      releases: vi.fn(panne),
      backends: vi.fn(panne),
      executors: vi.fn(() => Promise.resolve([])),
      findings: vi.fn(panne),
      memoryPending: vi.fn(panne),
    },
  };
});

async function rendre(page: ReactNode) {
  document.body.innerHTML = "";
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    render(
      <QueryClientProvider client={client}>
        <Suspense fallback="…">{page}</Suspense>
      </QueryClientProvider>,
    );
  });
}

describe("une lecture qui échoue se dit, elle ne passe pas pour du vide (S23-08)", () => {
  it("la vue d'ensemble : coûts, mesures, tickets", async () => {
    await rendre(<ProjectOverview params={Promise.resolve({ slug: "billing-api" })} />);
    expect(await screen.findByText(/could not read the costs: 503 Service Unavailable/)).toBeInTheDocument();
    expect(screen.getByText(/could not read the delivery measures/)).toBeInTheDocument();
    expect(screen.getByText(/could not read the tickets/)).toBeInTheDocument();
    expect(screen.queryByText("no spend recorded.")).toBeNull();
    expect(screen.queryByText("measurements being computed.")).toBeNull();
  });

  it("les trains : le train et l'historique, et « retry » relit", async () => {
    await rendre(<TrainsPage params={Promise.resolve({ slug: "billing-api" })} />);
    expect(await screen.findByText(/could not read the batches/)).toBeInTheDocument();
    expect(screen.getAllByText(/could not read the (prod|staging|[a-z]+) train/).length).toBeGreaterThan(0);
    expect(screen.queryByText("no batch")).toBeNull();
    const appels = vi.mocked(api.releases).mock.calls.length;
    await act(async () => {
      screen.getAllByRole("button", { name: "retry" }).at(-1)?.click();
    });
    expect(vi.mocked(api.releases).mock.calls.length).toBeGreaterThan(appels);
  });

  it("la plateforme : une panne n'est pas « no backend registered », un vide l'est", async () => {
    await rendre(<PlatformPage />);
    expect(await screen.findByText(/could not read the agent backends/)).toBeInTheDocument();
    expect(screen.queryByText("no backend registered")).toBeNull();
    expect(screen.getByText("no executor registered")).toBeInTheDocument();
  });

  it("les findings et les faits proposés", async () => {
    await rendre(<FindingsPage params={Promise.resolve({ slug: "billing-api" })} />);
    expect(await screen.findByText(/could not read the findings/)).toBeInTheDocument();
    await rendre(<MemoryPage params={Promise.resolve({ slug: "billing-api" })} />);
    expect(await screen.findByText(/could not read the proposed facts/)).toBeInTheDocument();
    // La recherche n'est pas lancée : elle ne dit rien, pas même « reading ».
    expect(screen.queryByText(/reading the memory/)).toBeNull();
  });
});
