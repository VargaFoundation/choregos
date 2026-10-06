import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DecisionBar } from "@/components/decision-bar";
import { api } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return { ...reel, api: { ...reel.api, decide: vi.fn() } };
});

const badge = {
  payload: {
    summary: "Badge",
    instructions: "Hand the badge over in person, then scan it.",
    form: {
      type: "object",
      required: ["badge_uid"],
      properties: { badge_uid: { type: "string", title: "badge UID" }, comment: { type: "string" } },
    },
    attest: "I handed the badge to its holder in person",
  },
};

describe("une tâche humaine : un formulaire, une attestation (S20-06)", () => {
  it("« done » reste fermé tant qu'un champ requis manque ou que rien n'est attesté", async () => {
    vi.mocked(api.decide).mockResolvedValue({});
    const fait = vi.fn();
    render(<DecisionBar itemId="it-1" kind="task" request={badge} onDone={fait} />);
    expect(screen.getByText("Hand the badge over in person, then scan it.")).toBeInTheDocument();
    const termine = screen.getByRole("button", { name: "done" });
    expect(termine).toBeDisabled();
    fireEvent.click(screen.getByLabelText(/I handed the badge to its holder in person/));
    expect(termine).toBeDisabled(); // attestée, mais le champ requis manque
    expect(screen.getByText("to fill: badge_uid")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/badge UID/), { target: { value: "04A1B2C3" } });
    expect(termine).toBeEnabled();
    fireEvent.click(screen.getByLabelText(/I handed the badge to its holder in person/));
    expect(termine).toBeDisabled(); // remplie, mais plus attestée
    fireEvent.click(screen.getByLabelText(/I handed the badge to its holder in person/));
    expect(termine).toBeEnabled();
    fireEvent.click(termine);
    await waitFor(() =>
      expect(api.decide).toHaveBeenCalledWith("it-1", {
        kind: "complete",
        values: { badge_uid: "04A1B2C3" },
        attested: true,
      }),
    );
    expect(fait).toHaveBeenCalled();
  });

  it("ne pas pouvoir la faire se dit, avec sa raison", async () => {
    vi.mocked(api.decide).mockResolvedValue({});
    render(<DecisionBar itemId="it-1" kind="task" request={badge} />);
    const impossible = screen.getByRole("button", { name: "cannot do it" });
    expect(impossible).toBeDisabled();
    fireEvent.change(screen.getByLabelText("why it cannot be done"), { target: { value: "printer down" } });
    fireEvent.click(impossible);
    await waitFor(() => expect(api.decide).toHaveBeenLastCalledWith("it-1", { kind: "reject", reason: "printer down" }));
  });

  it("une approbation garde sa barre", () => {
    render(<DecisionBar itemId="it-1" kind="approval" />);
    expect(screen.getByRole("button", { name: "approve" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "done" })).toBeNull();
  });
});
