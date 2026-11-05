import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { Suspense, type ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import WorkflowsPage from "@/app/p/[slug]/workflows/page";
import { RoutingCalibrationCard } from "@/components/workflows/routing-calibration";
import { api } from "@/lib/api";
import type { QuestionCalibration, RoutingCalibration, WorkflowRouting } from "@/lib/types";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), useSearchParams: () => new URLSearchParams() }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      workflows: vi.fn(),
      workflowRouting: vi.fn(),
      project: vi.fn().mockResolvedValue({ slug: "fon", org: "acme" }),
      routingCalibration: vi.fn(),
    },
  };
});

/** The routing's `workflow` question, measured on 42 observed routings, 0.70 suggested. */
const workflowQuestion = {
  question: "workflow",
  source: "router" as string as QuestionCalibration["source"],
  model: "platform/decide",
  model_version: "laya-0.4.21",
  count: 57,
  observed: 42,
  accuracy: 37 / 42,
  ece: 0.04,
  bins: [],
  thresholds: [
    { threshold: 0.5, count: 42, coverage: 1, accuracy: 37 / 42 },
    { threshold: 0.7, count: 36, coverage: 36 / 42, accuracy: 34 / 36 },
  ],
  suggested_threshold: 0.7,
  suggestion_reason: null,
};

function withClient(children: ReactNode) {
  return (
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <Suspense>{children}</Suspense>
    </QueryClientProvider>
  );
}

describe("the routing's calibration (S52-12, ADR 0123)", () => {
  it("measures the routing and compares the minimum confidence with the suggested threshold", async () => {
    vi.mocked(api.routingCalibration).mockResolvedValue({
      min_confidence: 0.5,
      target_accuracy: 0.9,
      min_observed: 30,
      questions: [workflowQuestion],
    } satisfies RoutingCalibration);
    render(withClient(<RoutingCalibrationCard slug="fon" />));
    expect(await screen.findByTestId("calibration-numbers-workflow")).toHaveTextContent("42 of 57 decisions observed");
    expect(screen.getByTestId("suggested-threshold-workflow")).toHaveTextContent("0.70");
    expect(screen.getByTestId("workflow-threshold-workflow")).toHaveTextContent(
      "The routing takes the model's answer from a probability ≥ 0.50, the minimum confidence: 100 % of the observed decisions reach it (42), 88 % of them right.",
    );
    expect(screen.getByTestId("threshold-below-workflow")).toHaveTextContent(
      "The minimum confidence, 0.50, is below the suggested 0.70",
    );
    expect(api.routingCalibration).toHaveBeenCalledWith("fon", 0.9);
  });

  it("says when the project sets no minimum confidence", async () => {
    vi.mocked(api.routingCalibration).mockResolvedValue({
      min_confidence: null,
      target_accuracy: 0.9,
      min_observed: 30,
      questions: [workflowQuestion],
    });
    render(withClient(<RoutingCalibrationCard slug="fon" />));
    expect(await screen.findByText("No minimum confidence is set: the routing takes every answer the model gives.")).toBeInTheDocument();
    expect(screen.queryByTestId("threshold-below-workflow")).not.toBeInTheDocument();
  });

  it("says nothing is measured before a routing is recorded", async () => {
    vi.mocked(api.routingCalibration).mockResolvedValue({ min_confidence: 0.5, target_accuracy: 0.9, min_observed: 30, questions: [] });
    render(withClient(<RoutingCalibrationCard slug="fon" />));
    expect(await screen.findByTestId("calibration-empty")).toHaveTextContent(
      "No routing recorded yet: the ledger measures it once a decision model has routed a work item",
    );
  });

  it("is on the workflows page once the strategy asks a model, not with labels alone", async () => {
    vi.mocked(api.workflows).mockResolvedValue([{ name: "onboarding", version: 1, is_default: true, open_items: 0 }]);
    vi.mocked(api.routingCalibration).mockResolvedValue({ min_confidence: 0.5, target_accuracy: 0.9, min_observed: 30, questions: [] });
    const routing = (strategy: WorkflowRouting["strategy"]): WorkflowRouting => ({ default: "onboarding", rules: [], strategy });

    vi.mocked(api.workflowRouting).mockResolvedValue(routing("rules_then_llm"));
    let view: ReturnType<typeof render> | undefined;
    await act(async () => {
      view = render(withClient(<WorkflowsPage params={Promise.resolve({ slug: "fon" })} />));
    });
    expect(await screen.findByText("How good the routing is")).toBeInTheDocument();
    view!.unmount();

    vi.mocked(api.workflowRouting).mockResolvedValue(routing("labels"));
    await act(async () => {
      render(withClient(<WorkflowsPage params={Promise.resolve({ slug: "fon" })} />));
    });
    await screen.findByRole("form", { name: "New routing rule" });
    expect(screen.queryByText("How good the routing is")).not.toBeInTheDocument();
  });
});

describe("the routing calibration client (S52-12)", () => {
  it("asks the API's path with the target accuracy", async () => {
    const real = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
    const body = { min_confidence: 0.75, target_accuracy: 0.8, min_observed: 30, questions: [] };
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const calibration = await real.api.routingCalibration("acme:fon", 0.8);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/projects/acme:fon/workflow-routing/calibration?target=0.8",
      expect.objectContaining({ credentials: "include" }),
    );
    expect(calibration.min_confidence).toBe(0.75);
    vi.unstubAllGlobals();
  });
});
