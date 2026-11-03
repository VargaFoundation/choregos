import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DraftBar } from "@/components/workflows/draft-bar";
import { DraftProvider, useDraft } from "@/components/workflows/draft";
import { operations } from "@/components/workflows/operations";
import { StepForm, StatePanel, TransitionPanel } from "@/components/workflows/panels";
import { ApiError, api } from "@/lib/api";
import type { WorkflowEditResult, WorkflowOperation } from "@/lib/types";
import fixture from "./fixtures/gestes-de-la-console.json";

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
    api: {
      ...reel.api,
      workflowNamed: vi.fn(),
      validateWorkflow: vi.fn(),
      editWorkflow: vi.fn(),
      putWorkflowNamed: vi.fn(),
    },
  };
});

/**
 * Every console gesture emits the operation the core grafts (S16-11). The same file is read by
 * `packages/core/tests/test_edition.py`: a field renamed on one side turns the other red.
 */
describe("the console's gestures", () => {
  type Gesture = keyof typeof operations;
  it.each(fixture.gestes.map((g) => [g.geste, g.args, g.operation] as const))(
    "%s(%j) emits the expected operation",
    (gesture, args, operation) => {
      const build = operations[gesture as Gesture] as (...values: unknown[]) => WorkflowOperation;
      expect(build(...args)).toEqual(operation);
    },
  );

  it("every console gesture is covered by the shared file", () => {
    expect(new Set(fixture.gestes.map((g) => g.geste))).toEqual(new Set(Object.keys(operations)));
  });
});

const YAML = fixture.workflow;
const graph = {
  nodes: [
    { id: "inbox", display: "To triage", kind: "wait", lane: "agent" },
    { id: "ready", display: "Ready", kind: "normal", lane: "agent" },
    { id: "done", display: "Fini", kind: "normal", terminal: true, lane: "terminal" },
  ],
  edges: [{ id: "t-implement", from: "ready", to: "done", kind: "nominal", actor: "dev", gates: ["scope_respected"] }],
};

function result(yaml: string, inverse: WorkflowOperation[]): WorkflowEditResult {
  return { valid: true, errors: [], warnings: [], graph: graph, process: [], yaml, diff: `+${yaml}`, inverse, notices: [] };
}

/** A probe: the draft's text, and a gesture on demand. */
function Sonde({ gesture }: { gesture?: WorkflowOperation }) {
  const draft = useDraft();
  return (
    <div>
      <p data-testid="yaml">{draft.yaml}</p>
      {gesture && (
        <button type="button" onClick={() => void draft.apply(gesture)}>
          gesture
        </button>
      )}
    </div>
  );
}

/** Renders under a draft, once the workflow is read: that is where the map opens its panels. */
async function renderUnderDraft(contenu: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <DraftProvider slug="rh" name="onboarding">
        <DraftBar />
        {contenu}
      </DraftProvider>
    </QueryClientProvider>,
  );
  await waitFor(() => expect(screen.getByTestId("yaml")).toHaveTextContent("apiVersion"));
}

beforeEach(() => {
  vi.mocked(api.workflowNamed).mockResolvedValue({
    name: "onboarding",
    version: 3,
    yaml: YAML,
    is_active: true,
    json: { actors: { refiner: {}, owner: {}, dev: {} } },
  } as never);
  vi.mocked(api.validateWorkflow).mockResolvedValue({ valid: true, errors: [], warnings: [], graph: graph, process: [] });
  vi.mocked(api.editWorkflow).mockReset();
  vi.mocked(api.putWorkflowNamed).mockReset();
});

describe("a workflow's draft", () => {
  it("a gesture starts from the text read; undo replays the inverse of the LAST gesture, then of the one before", async () => {
    const gesture = operations.libelle("inbox", "New requests");
    const inverseA = [operations.libelle("inbox", "To triage")];
    const inverseB = [operations.libelle("inbox", "New requests")];
    vi.mocked(api.editWorkflow)
      .mockResolvedValueOnce(result("apres-A", inverseA))
      .mockResolvedValueOnce(result("apres-B", inverseB))
      .mockResolvedValueOnce(result("apres-A", [gesture]))
      .mockResolvedValueOnce(result(YAML, [gesture]));
    await renderUnderDraft(<Sonde gesture={gesture} />);

    fireEvent.click(screen.getByRole("button", { name: "gesture" }));
    await screen.findByText("1 change not published yet");
    expect(api.editWorkflow).toHaveBeenNthCalledWith(1, YAML, [gesture]);
    fireEvent.click(screen.getByRole("button", { name: "gesture" }));
    await screen.findByText("2 changes not published yet");
    // The second gesture is grafted onto the already edited text, not onto the one that was read.
    expect(api.editWorkflow).toHaveBeenNthCalledWith(2, "apres-A", [gesture]);
    expect(screen.getByTestId("yaml")).toHaveTextContent("apres-B");

    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await screen.findByText("1 change not published yet");
    expect(api.editWorkflow).toHaveBeenNthCalledWith(3, "apres-B", inverseB);
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(screen.queryByTestId("draft")).toBeNull());
    expect(api.editWorkflow).toHaveBeenNthCalledWith(4, "apres-A", inverseA);
  });

  it("before the workflow is read, a gesture does not go out on an empty text", async () => {
    vi.mocked(api.workflowNamed).mockReturnValue(new Promise(() => {}));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <DraftProvider slug="rh" name="onboarding">
          <Sonde gesture={operations.retirerLEtat("x")} />
        </DraftProvider>
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "gesture" }));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(api.editWorkflow).not.toHaveBeenCalled();
  });

  it("publishing sends the text with the version read; a 409 says so without overwriting anything", async () => {
    vi.mocked(api.editWorkflow).mockResolvedValue(result("editee", []));
    vi.mocked(api.putWorkflowNamed).mockRejectedValue(new ApiError(409, { title: "Conflict" }));
    await renderUnderDraft(<Sonde gesture={operations.genre("ready", "wait")} />);
    fireEvent.click(screen.getByRole("button", { name: "gesture" }));

    fireEvent.click(await screen.findByRole("button", { name: "Publish v4" }));
    await screen.findByText(/Someone published a newer version meanwhile/);
    expect(api.putWorkflowNamed).toHaveBeenCalledWith("rh", "onboarding", "editee", 3);
    // The draft stays: nothing is lost, the author chooses to discard.
    expect(screen.getByText("1 change not published yet")).toBeInTheDocument();
  });

  it("a gesture refused by the API leaves the text as it is and says why", async () => {
    vi.mocked(api.editWorkflow).mockRejectedValue(new ApiError(422, { title: "Refused", detail: "unknown state: x" }));
    await renderUnderDraft(<Sonde gesture={operations.retirerLEtat("x")} />);
    fireEvent.click(screen.getByRole("button", { name: "gesture" }));
    await screen.findByText("unknown state: x");
    expect(screen.getByTestId("yaml")).toHaveTextContent("apiVersion");
  });
});

describe("the panels of the map and of the process view", () => {
  const states = graph.nodes;

  it("a state's panel emits the label, the rename and the transition entered", async () => {
    vi.mocked(api.editWorkflow).mockResolvedValue(result(YAML, []));
    await renderUnderDraft(
      <>
        <Sonde />
        <StatePanel node={states[0]!} states={states} actors={["refiner", "owner"]} />
      </>,
    );
    const panel = screen.getByTestId("panneau-etat");

    fireEvent.change(screen.getByLabelText("Label"), { target: { value: "New requests" } });
    fireEvent.click(screen.getByRole("button", { name: "Set label" }));
    await waitFor(() =>
      expect(api.editWorkflow).toHaveBeenLastCalledWith(YAML, [operations.libelle("inbox", "New requests")]),
    );

    fireEvent.change(screen.getByLabelText("Name (every reference follows)"), { target: { value: "triage" } });
    fireEvent.click(screen.getByRole("button", { name: "Rename" }));
    await waitFor(() => expect(api.editWorkflow).toHaveBeenLastCalledWith(YAML, [operations.renommer("inbox", "triage")]));

    fireEvent.change(screen.getByLabelText("New transition to"), { target: { value: "ready" } });
    fireEvent.change(screen.getByLabelText("By"), { target: { value: "owner" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() =>
      expect(api.editWorkflow).toHaveBeenLastCalledWith(YAML, [operations.ajouterUneTransition("inbox", "ready", "owner")]),
    );

    // A new state and the transition leading to it go out in one gesture: a single "undo" reverts both.
    fireEvent.change(screen.getByLabelText("New transition to"), { target: { value: "(new)" } });
    fireEvent.change(screen.getByLabelText("Name of the new state"), { target: { value: "verification" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() =>
      expect(api.editWorkflow).toHaveBeenLastCalledWith(YAML, [
        operations.ajouterUnEtat("verification", "verification"),
        operations.ajouterUneTransition("inbox", "verification", "owner"),
      ]),
    );
    expect(panel).toBeInTheDocument();
  });

  it("a step is added without choosing a state first: it says where it starts from (S23-13)", async () => {
    vi.mocked(api.editWorkflow).mockResolvedValue(result(YAML, []));
    await renderUnderDraft(
      <>
        <Sonde />
        <StepForm states={states} actors={["refiner", "owner"]} />
      </>,
    );
    const ajouter = screen.getByRole("button", { name: "Add" });
    expect(ajouter).toBeDisabled();
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "inbox" } });
    fireEvent.change(screen.getByLabelText("To"), { target: { value: "(new)" } });
    fireEvent.change(screen.getByLabelText("Name of the new state"), { target: { value: "in_review" } });
    fireEvent.click(ajouter);
    await waitFor(() =>
      expect(api.editWorkflow).toHaveBeenLastCalledWith(YAML, [
        operations.ajouterUnEtat("in_review", "in_review"),
        operations.ajouterUneTransition("inbox", "in_review", "refiner"),
      ]),
    );
  });

  it("a transition's panel emits the actor, the gate, the time limit and their removal", async () => {
    vi.mocked(api.editWorkflow).mockResolvedValue(result(YAML, []));
    await renderUnderDraft(
      <>
        <Sonde />
        <TransitionPanel edge={{ ...graph.edges[0]!, timeout_hours: 72 }} actors={["dev", "owner"]} />
      </>,
    );

    fireEvent.change(screen.getByLabelText("Who moves it"), { target: { value: "owner" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Set" })[0]!);
    await waitFor(() => expect(api.editWorkflow).toHaveBeenLastCalledWith(YAML, [operations.acteur("t-implement", "owner")]));

    fireEvent.click(screen.getByRole("button", { name: "Remove the guarantee scope_respected" }));
    await waitFor(() =>
      expect(api.editWorkflow).toHaveBeenLastCalledWith(YAML, [operations.retirerUneGarantie("t-implement", "scope_respected")]),
    );

    fireEvent.change(screen.getByLabelText("Guarantee to add"), { target: { value: "ci_green" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() =>
      expect(api.editWorkflow).toHaveBeenLastCalledWith(YAML, [operations.ajouterUneGarantie("t-implement", "ci_green")]),
    );

    // The time limit read is prefilled; emptied, it removes the limit.
    const limit = screen.getByLabelText("At most (hours, empty: the default)");
    expect(limit).toHaveValue(72);
    fireEvent.change(limit, { target: { value: "" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Set" })[1]!);
    await waitFor(() => expect(api.editWorkflow).toHaveBeenLastCalledWith(YAML, [operations.delai("t-implement", null)]));
  });

  it("says each guarantee in words with its threshold, what each actor is, and how long the step waits", async () => {
    await renderUnderDraft(
      <>
        <Sonde />
        <TransitionPanel
          edge={{ ...graph.edges[0]!, gates: ["scope_respected", "diff_size_max"] }}
          actors={["dev", "owner"]}
          definitions={{ dev: { type: "agent", role: "implement" }, owner: { type: "human", group: "product-owners" } }}
          gates={[
            { name: "scope_respected", summary: "the agent stayed within its allowed paths", detail: null },
            { name: "diff_size_max", summary: "the change stays under the size limit", detail: "at most 10 files and 400 lines changed" },
          ]}
          timing="It waits for ci green up to 24 h (no limit set)."
        />
      </>,
    );
    const gateList = screen.getByTestId("garanties-de-la-transition");
    expect(gateList).toHaveTextContent("diff_size_max — the change stays under the size limitat most 10 files and 400 lines changed");
    expect(screen.getByRole("option", { name: "owner — person · product-owners" })).toBeInTheDocument();
    expect(screen.getByTestId("delai-en-clair")).toHaveTextContent("It waits for ci green up to 24 h");
  });

  it("a secondary arrow is not edited here: the panel points to the YAML", async () => {
    await renderUnderDraft(
      <>
        <Sonde />
        <TransitionPanel edge={{ from: "ready", to: "inbox", kind: "reject" }} actors={[]} />
      </>,
    );
    expect(screen.getByText(/edit it in the YAML/)).toBeInTheDocument();
    expect(screen.queryByTestId("panneau-transition")).toBeNull();
  });
});
