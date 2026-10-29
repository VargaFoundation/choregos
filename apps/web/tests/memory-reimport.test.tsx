import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Suspense, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ProjectMemoryPage from "@/app/p/[slug]/memory/page";
import { api } from "@/lib/api";

/** Re-importing a project's memory from its Memory page (S47-17): confirmed, and only by who may write it. */
const session = vi.hoisted(() => ({
  me: { memberships: [] } as { memberships: Array<Record<string, unknown>> },
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), usePathname: () => "/p/billing-api/memory" }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme", me: session.me }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      projectMemories: vi.fn(() => Promise.resolve([])),
      agents: vi.fn(() => Promise.resolve([])),
      memoryPending: vi.fn(() => Promise.resolve([])),
      memorySearch: vi.fn(() => Promise.resolve([])),
      reimportMemory: vi.fn(() => Promise.resolve(undefined)),
    },
  };
});

async function show(node: ReactNode) {
  await act(async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <Suspense fallback="…">{node}</Suspense>
      </QueryClientProvider>,
    );
  });
}
const open = () => show(<ProjectMemoryPage params={Promise.resolve({ slug: "billing-api" })} />);

beforeEach(() => {
  vi.clearAllMocks();
  document.body.innerHTML = "";
});

describe("the project memory's re-import", () => {
  it("re-imports after confirmation and hides without the right", async () => {
    session.me = { memberships: [{ org: "acme", role: "project_owner", project_slug: "billing-api" }] };
    await open();
    fireEvent.click(await screen.findByRole("button", { name: "Re-import the project memory" }));
    // The first click only asks: nothing is sent before the confirmation.
    expect(api.reimportMemory).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Re-import" }));
    await waitFor(() => expect(api.reimportMemory).toHaveBeenCalledTimes(1));
    expect(api.reimportMemory).toHaveBeenCalledWith("billing-api");
    expect(await screen.findByRole("status")).toHaveTextContent(/Re-import scheduled/);

    document.body.innerHTML = "";
    session.me = { memberships: [{ org: "acme", role: "developer", project_slug: "billing-api" }] };
    await open();
    await screen.findByLabelText("Memory");
    expect(screen.queryByRole("button", { name: "Re-import the project memory" })).toBeNull();
  });

  it("an organisation administrator may re-import; cancelling sends nothing", async () => {
    session.me = { memberships: [{ org: "acme", role: "org_admin" }] };
    await open();
    fireEvent.click(await screen.findByRole("button", { name: "Re-import the project memory" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(api.reimportMemory).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Re-import the project memory" })).toBeInTheDocument();
  });

  it("keeps the confirmation open and says the refusal", async () => {
    session.me = { memberships: [{ org: "acme", role: "org_admin" }] };
    vi.mocked(api.reimportMemory).mockRejectedValueOnce(new Error("memory:write required"));
    await open();
    fireEvent.click(await screen.findByRole("button", { name: "Re-import the project memory" }));
    fireEvent.click(screen.getByRole("button", { name: "Re-import" }));
    expect(await screen.findByText("memory:write required")).toBeInTheDocument();
    expect(screen.queryByText(/Re-import scheduled/)).toBeNull();
  });
});
