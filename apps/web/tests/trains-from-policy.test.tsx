import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { Suspense } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import TrainsPage from "@/app/p/[slug]/trains/page";
import { api } from "@/lib/api";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), usePathname: () => "/" }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "varga", me: null }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
    api: {
      ...reel.api,
      policy: vi.fn(),
      train: vi.fn((_slug: string, env: string) =>
        Promise.resolve({ env, status: "collecting", batch_size: 0, pending_items: [], frozen: false, window_open: true }),
      ),
      releases: vi.fn(() => Promise.resolve({ items: [], meta: { has_more: false } })),
    },
  };
});

function policyWith(releaseTrain: Record<string, object> | undefined) {
  return { name: "p", version: 1, yaml: "", is_active: true, json: releaseTrain ? { release_train: releaseTrain } : {} };
}

async function renderPage() {
  document.body.innerHTML = "";
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    render(
      <QueryClientProvider client={client}>
        <Suspense fallback="…">
          <TrainsPage params={Promise.resolve({ slug: "billing-api" })} />
        </Suspense>
      </QueryClientProvider>,
    );
  });
}

describe("the trains page lists the environments of the policy (S26-05)", () => {
  beforeEach(() => vi.mocked(api.train).mockClear());

  it("a policy with prod only shows one train", async () => {
    vi.mocked(api.policy).mockResolvedValue(policyWith({ prod: { mode: "train" } }) as never);
    await renderPage();
    expect(await screen.findByText("Environment prod")).toBeInTheDocument();
    expect(screen.queryByText("Environment staging")).toBeNull();
    expect(vi.mocked(api.train).mock.calls.map((call) => call[1])).toEqual(["prod"]);
  });

  it("follows the policy's environments and order, dev included", async () => {
    vi.mocked(api.policy).mockResolvedValue(
      policyWith({ dev: { mode: "auto_sync" }, qa: { mode: "train" }, prod: { mode: "train" } }) as never,
    );
    await renderPage();
    await screen.findByText("Environment prod");
    const titles = screen.getAllByText(/^Environment /).map((node) => node.textContent);
    expect(titles).toEqual(["Environment dev", "Environment qa", "Environment prod"]);
  });

  it("a policy without release_train says so, and asks for no train", async () => {
    vi.mocked(api.policy).mockResolvedValue(policyWith(undefined) as never);
    await renderPage();
    expect(await screen.findByText("No release train")).toBeInTheDocument();
    expect(api.train).not.toHaveBeenCalled();
  });
});
