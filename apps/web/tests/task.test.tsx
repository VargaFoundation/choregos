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

describe("a human task: a form, an attestation (S20-06)", () => {
  it("\"done\" stays closed while a required field is missing or nothing is attested", async () => {
    vi.mocked(api.decide).mockResolvedValue({});
    const done = vi.fn();
    render(<DecisionBar itemId="it-1" kind="task" request={badge} onDone={done} />);
    expect(screen.getByText("Hand the badge over in person, then scan it.")).toBeInTheDocument();
    const termine = screen.getByRole("button", { name: "Done" });
    expect(termine).toBeDisabled();
    fireEvent.click(screen.getByLabelText(/I handed the badge to its holder in person/));
    expect(termine).toBeDisabled(); // attested, but the required field is missing
    expect(screen.getByText("To fill: badge_uid")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/badge UID/), { target: { value: "04A1B2C3" } });
    expect(termine).toBeEnabled();
    fireEvent.click(screen.getByLabelText(/I handed the badge to its holder in person/));
    expect(termine).toBeDisabled(); // filled in, but no longer attested
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
    expect(done).toHaveBeenCalled();
  });

  it("not being able to do it is said, with its reason", async () => {
    vi.mocked(api.decide).mockResolvedValue({});
    render(<DecisionBar itemId="it-1" kind="task" request={badge} />);
    const impossible = screen.getByRole("button", { name: "Cannot do it" });
    expect(impossible).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Why it cannot be done"), { target: { value: "printer down" } });
    fireEvent.click(impossible);
    await waitFor(() => expect(api.decide).toHaveBeenLastCalledWith("it-1", { kind: "reject", reason: "printer down" }));
  });

  it("an approval keeps its bar", () => {
    render(<DecisionBar itemId="it-1" kind="approval" />);
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Done" })).toBeNull();
  });
});
