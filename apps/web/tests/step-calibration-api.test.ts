import { describe, expect, it, vi } from "vitest";

describe("a step's calibration (S52-05, ADR 0121)", () => {
  it("asks the contract's path for the step, its workflow named safely, with the target accuracy", async () => {
    const body = { workflow: "route by team", step: "inbox", target_accuracy: 0.8, min_observed: 30, questions: [] };
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const { api } = await import("@/lib/api");
    const calibration = await api.stepCalibration("acme:billing-api", "route by team", "inbox", 0.8);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/projects/acme:billing-api/workflows/route%20by%20team/steps/inbox/calibration?target=0.8",
      expect.objectContaining({ credentials: "include" }),
    );
    expect(calibration.min_observed).toBe(30);
    vi.unstubAllGlobals();
  });
});
