import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { checklistSentence, LifecycleBadges, LifecyclePanel, nextMoves } from "@/components/agents/lifecycle";
import { ApiError, api } from "@/lib/api";
import type { Agent } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return { ...reel, api: { ...reel.api, changeAgentLifecycle: vi.fn(), transferAgentOwnership: vi.fn() } };
});

const agent = (over: Partial<Agent> = {}): Agent => ({
  slug: "onboarding-coordinator",
  kind: "internal",
  display_name: "Onboarding",
  status: "active",
  latest_version: 1,
  lifecycle: "active",
  unused: false,
  ...over,
});

function wrap(children: ReactNode) {
  return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}

describe("the lifecycle of an agent, in the console (S32-04)", () => {
  it("retired is final; a draft is activated or retired", () => {
    expect(nextMoves("draft")).toEqual(["active", "retired"]);
    expect(nextMoves("deprecated")).toEqual(["active", "retired"]);
    expect(nextMoves("retired")).toEqual([]);
  });

  it("an unused or deprecated agent says so; an active one shows nothing more", () => {
    const { rerender } = render(<LifecycleBadges agent={agent()} />);
    expect(screen.queryByTestId("cycle")).toBeNull();
    rerender(<LifecycleBadges agent={agent({ lifecycle: "deprecated", unused: true })} />);
    expect(screen.getByTestId("cycle").textContent).toContain("deprecated");
    expect(screen.getByTestId("inutilise").textContent).toContain("unused for 60 days");
  });

  it("the checklist reads in one sentence", () => {
    expect(
      checklistSentence({
        agent: agent({ lifecycle: "retired" }),
        revoked_credentials: 2,
        unpinned_projects: ["billing-api"],
        running_work_items: ["HR-3"],
        forced: true,
      }),
    ).toBe("Retired: 2 credentials revoked, unpinned from billing-api, new runs refused, forced past the runs of HR-3.");
  });

  it("retiring with runs in flight offers to force, and forcing says what it did", async () => {
    vi.mocked(api.changeAgentLifecycle)
      .mockRejectedValueOnce(new ApiError(409, { detail: "agent `onboarding-coordinator` has runs in flight on HR-3" }))
      .mockResolvedValueOnce({
        agent: agent({ lifecycle: "retired" }),
        revoked_credentials: 0,
        unpinned_projects: [],
        running_work_items: ["HR-3"],
        forced: true,
      });
    render(wrap(<LifecyclePanel org="varga" agent={agent()} />));
    fireEvent.click(screen.getByRole("button", { name: "Retire…" }));
    fireEvent.click(screen.getByRole("button", { name: "Retire" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("HR-3"));
    fireEvent.click(screen.getByRole("button", { name: "Retire anyway" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("forced past the runs of HR-3"));
    expect(api.changeAgentLifecycle).toHaveBeenLastCalledWith("varga", "onboarding-coordinator", { to: "retired", force: true });
  });
});
