import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { BrouillonProvider, useBrouillon } from "@/components/workflows/brouillon";
import { operations } from "@/components/workflows/operations";
import { api } from "@/lib/api";
import type { WorkflowEditResult, WorkflowOperation } from "@/lib/types";
import fixture from "./fixtures/gestes-de-la-console.json";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: { ...real.api, workflowNamed: vi.fn(), validateWorkflow: vi.fn(), editWorkflow: vi.fn() },
  };
});

const YAML = fixture.workflow;

function result(yaml: string, inverse: WorkflowOperation[]): WorkflowEditResult {
  return { valid: true, errors: [], warnings: [], graph: undefined, process: [], yaml, diff: "", inverse, notices: [] };
}

/** Two gestures in a row, the second started before React has rendered the first. */
function TwoGestures({ gesture }: { gesture: WorkflowOperation }) {
  const draft = useBrouillon();
  return (
    <div>
      <p data-testid="yaml">{draft.yaml}</p>
      <button
        type="button"
        onClick={async () => {
          await draft.appliquer(gesture);
          await draft.appliquer(gesture);
        }}
      >
        twice
      </button>
    </div>
  );
}

describe("a workflow draft under quick gestures (#678)", () => {
  it("grafts the second gesture onto the first one's text, not onto the text last rendered", async () => {
    vi.mocked(api.workflowNamed).mockResolvedValue({ name: "onboarding", version: 3, yaml: YAML, is_active: true } as never);
    vi.mocked(api.validateWorkflow).mockResolvedValue({ valid: true, errors: [], warnings: [], process: [] });
    const gesture = operations.libelle("inbox", "New requests");
    vi.mocked(api.editWorkflow)
      .mockResolvedValueOnce(result("after-A", []))
      .mockResolvedValueOnce(result("after-B", []));
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <BrouillonProvider slug="rh" name="onboarding">
          <TwoGestures gesture={gesture} />
        </BrouillonProvider>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("yaml")).toHaveTextContent("apiVersion"));
    fireEvent.click(screen.getByRole("button", { name: "twice" }));
    await waitFor(() => expect(screen.getByTestId("yaml")).toHaveTextContent("after-B"));
    expect(vi.mocked(api.editWorkflow).mock.calls.map((call) => call[0])).toEqual([YAML, "after-A"]);
  });
});
