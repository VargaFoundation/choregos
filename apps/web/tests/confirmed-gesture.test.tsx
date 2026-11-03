import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ConfirmedGesture } from "@/components/confirmed-gesture";

describe("a confirmed gesture (S23-07)", () => {
  it("does not leave at the first click, says the API's refusal on the spot, and stays open", async () => {
    const action = vi.fn().mockRejectedValueOnce(new Error("409: the last administrator stays")).mockResolvedValue(undefined);
    const done = vi.fn();
    render(
      <ConfirmedGesture question="remove alice?" confirmer="remove alice" action={action} onFait={done}>
        remove
      </ConfirmedGesture>,
    );
    fireEvent.click(screen.getByRole("button", { name: "remove" }));
    expect(action).not.toHaveBeenCalled();
    expect(screen.getByRole("group", { name: "remove alice?" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "remove alice" }));
    expect(await screen.findByText("409: the last administrator stays")).toBeInTheDocument();
    expect(done).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "remove alice" }));
    await waitFor(() => expect(done).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("group")).toBeNull();
  });

  it("a required reason blocks execution while it is empty, and leaves with the gesture", async () => {
    const action = vi.fn().mockResolvedValue(undefined);
    render(
      <ConfirmedGesture question="abort?" confirmer="abort it" raison={{ label: "why", requise: true }} action={action}>
        abort
      </ConfirmedGesture>,
    );
    fireEvent.click(screen.getByRole("button", { name: "abort" }));
    expect(screen.getByRole("button", { name: "abort it" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/why/), { target: { value: "  not reversible " } });
    fireEvent.click(screen.getByRole("button", { name: "abort it" }));
    await waitFor(() => expect(action).toHaveBeenCalledWith("not reversible"));
  });
});
