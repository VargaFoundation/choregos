import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DecisionDAction } from "@/components/actions/decision";
import { propose } from "@/components/actions/statut";
import { api } from "@/lib/api";
import type { Action } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return { ...reel, api: { ...reel.api, decideAction: vi.fn() } };
});

const action = { id: "act-1", origin: "tool", kind: "k", title: "t", status: "pending_approval", approval: { step_up_minutes: 10 } } as Action;

function rendre(a: Action = action) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <DecisionDAction projet="rh" action={a} />
    </QueryClientProvider>,
  );
}

describe("décider d'une action gouvernée (ADR 0035, S20-02)", () => {
  it("un rejet dit pourquoi : le bouton reste fermé sans raison", async () => {
    vi.mocked(api.decideAction).mockResolvedValue(action);
    rendre();
    const rejeter = screen.getByRole("button", { name: "reject" });
    expect(rejeter).toBeDisabled();
    fireEvent.change(screen.getByLabelText("reason to reject"), { target: { value: "pas encore embauchée" } });
    expect(rejeter).toBeEnabled();
    fireEvent.click(rejeter);
    await waitFor(() =>
      expect(api.decideAction).toHaveBeenCalledWith("rh", "act-1", { decision: "reject", reason: "pas encore embauchée" }),
    );
  });

  it("approuver n'envoie pas de raison, et une action décidée ne se redécide pas", async () => {
    vi.mocked(api.decideAction).mockResolvedValue(action);
    const { unmount } = rendre();
    fireEvent.click(screen.getByRole("button", { name: "approve" }));
    await waitFor(() => expect(api.decideAction).toHaveBeenLastCalledWith("rh", "act-1", { decision: "approve" }));
    unmount();
    rendre({ ...action, status: "approved" });
    expect(screen.queryByTestId("decision")).toBeNull();
  });

  it("un agent qui propose se nomme comme tel", () => {
    expect(propose({ kind: "agent", id: "agent:coordinateur" })).toBe("the agent coordinateur");
    expect(propose({ kind: "user", id: "lea@varga.dev" })).toBe("lea@varga.dev");
  });
});
