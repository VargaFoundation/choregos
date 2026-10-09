import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { GesteConfirme } from "@/components/geste-confirme";

describe("un geste confirmé (S23-07)", () => {
  it("ne part pas au premier clic, dit le refus de l'API sur place, et reste ouvert", async () => {
    const action = vi.fn().mockRejectedValueOnce(new Error("409: the last administrator stays")).mockResolvedValue(undefined);
    const fait = vi.fn();
    render(
      <GesteConfirme question="remove alice?" confirmer="remove alice" action={action} onFait={fait}>
        remove
      </GesteConfirme>,
    );
    fireEvent.click(screen.getByRole("button", { name: "remove" }));
    expect(action).not.toHaveBeenCalled();
    expect(screen.getByRole("group", { name: "remove alice?" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "remove alice" }));
    expect(await screen.findByText("409: the last administrator stays")).toBeInTheDocument();
    expect(fait).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "remove alice" }));
    await waitFor(() => expect(fait).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("group")).toBeNull();
  });

  it("une raison requise bloque l'exécution tant qu'elle est vide, et part avec le geste", async () => {
    const action = vi.fn().mockResolvedValue(undefined);
    render(
      <GesteConfirme question="abort?" confirmer="abort it" raison={{ label: "why", requise: true }} action={action}>
        abort
      </GesteConfirme>,
    );
    fireEvent.click(screen.getByRole("button", { name: "abort" }));
    expect(screen.getByRole("button", { name: "abort it" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/why/), { target: { value: "  not reversible " } });
    fireEvent.click(screen.getByRole("button", { name: "abort it" }));
    await waitFor(() => expect(action).toHaveBeenCalledWith("not reversible"));
  });
});
