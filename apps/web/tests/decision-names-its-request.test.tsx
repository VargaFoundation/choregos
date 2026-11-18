import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DecisionBar } from "@/components/decision-bar";
import { api } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return { ...reel, api: { ...reel.api, decide: vi.fn() } };
});

// FON-319 (2026-11-18): the bar under a budget escalation sent no `request_id`, so the API decided the
// newest open request of the work item, the step `needs_human -> abandoned`, and abandoned it.
describe("a decision names the request it was shown with", () => {
  it("an approval carries the request's id", async () => {
    vi.mocked(api.decide).mockResolvedValue({});
    render(
      <DecisionBar itemId="w1" kind="approval" request={{ id: "hr-budget", payload: { summary: "budget" } }} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(api.decide).toHaveBeenLastCalledWith("w1", { request_id: "hr-budget", kind: "approve" }));
  });

  it("a send-back carries it too", async () => {
    vi.mocked(api.decide).mockResolvedValue({});
    render(<DecisionBar itemId="w1" kind="approval" request={{ id: "hr-1", payload: {} }} />);
    fireEvent.click(screen.getByRole("button", { name: "Send back" }));
    await waitFor(() =>
      expect(api.decide).toHaveBeenLastCalledWith("w1", { request_id: "hr-1", kind: "reject", reason: "sent back" }),
    );
  });

  it("without a request (an old caller), nothing is invented", async () => {
    vi.mocked(api.decide).mockResolvedValue({});
    render(<DecisionBar itemId="w1" kind="approval" />);
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(api.decide).toHaveBeenLastCalledWith("w1", { kind: "approve" }));
  });
});
