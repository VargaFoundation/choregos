import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DecisionDAction } from "@/components/actions/decision";
import { propose } from "@/components/actions/status";
import { api } from "@/lib/api";
import type { Action } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return { ...reel, api: { ...reel.api, decideAction: vi.fn() } };
});

const action = { id: "act-1", origin: "tool", kind: "k", title: "t", status: "pending_approval", approval: { step_up_minutes: 10 } } as Action;

function mount(a: Action = action) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <DecisionDAction projet="rh" action={a} />
    </QueryClientProvider>,
  );
}

describe("deciding on a governed action (ADR 0035, S20-02)", () => {
  it("a rejection says why: the button stays closed without a reason", async () => {
    vi.mocked(api.decideAction).mockResolvedValue(action);
    mount();
    const rejeter = screen.getByRole("button", { name: "Reject" });
    expect(rejeter).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Reason to reject"), { target: { value: "not hired yet" } });
    expect(rejeter).toBeEnabled();
    fireEvent.click(rejeter);
    await waitFor(() =>
      expect(api.decideAction).toHaveBeenCalledWith("rh", "act-1", { decision: "reject", reason: "not hired yet" }),
    );
  });

  it("approving sends no reason, and a decided action is not decided again", async () => {
    vi.mocked(api.decideAction).mockResolvedValue(action);
    const { unmount } = mount();
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(api.decideAction).toHaveBeenLastCalledWith("rh", "act-1", { decision: "approve" }));
    unmount();
    mount({ ...action, status: "approved" });
    expect(screen.queryByTestId("decision")).toBeNull();
  });

  it("an agent that proposes is named as such", () => {
    expect(propose({ kind: "agent", id: "agent:coordinateur" })).toBe("the agent coordinateur");
    expect(propose({ kind: "user", id: "lea@acme.dev" })).toBe("lea@acme.dev");
  });

  it("a job step that proposes is named as a job, not as an agent (#1364)", () => {
    expect(propose({ kind: "job", id: "job:snapshot", run_id: "r1" })).toBe("the job step snapshot");
  });
});
