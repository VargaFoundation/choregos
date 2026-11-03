import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SnakeMap } from "@/components/journey/journey-map";
import { ActorsPanel } from "@/components/workflows/actors-panel";
import { DraftProvider, useDraft } from "@/components/workflows/draft";
import { WorkflowMap } from "@/components/workflows/map/workflow-map";
import { KIND_COLOURS, DECISION_HUE, actorKind, KIND_LABEL } from "@/components/workflows/map/layout";
import { decisionEdits, decisionProblems, draftOf, type DecisionDraft } from "@/components/workflows/decision-actor-form";
import { decisionActorOf, decisionOutputs, describeActor, type WorkflowDecisionActor, type WorkflowDocument } from "@/components/workflows/document";
import { actorLabel } from "@/components/workflows/panels";
import { StepDetails } from "@/components/workflows/step-details";
import { workflowThresholds } from "@/components/workflows/decision-calibration";
import { api } from "@/lib/api";
import type { Agent, AgentCatalogueEntry, ProcessStep, QuestionCalibration, StepCalibration, WorkflowEditResult, WorkflowGraph } from "@/lib/types";
import { journeyInProgress } from "@/mocks/journey";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      workflowNamed: vi.fn(),
      validateWorkflow: vi.fn(),
      editWorkflow: vi.fn(),
      agents: vi.fn(),
      agentCatalogue: vi.fn(),
      stepCalibration: vi.fn(),
    },
  };
});

/** A decision step as ADR 0121 writes it: a choice and a yes/no, no model and no timeout written. */
const ROUTER: WorkflowDecisionActor = {
  type: "decision",
  role: "route_ticket",
  state: ["title", "description"],
  questions: {
    route: { type: "choice", instructions: "Which team?", options: ["billing", "tech"] },
    urgent: { type: "yes_no", instructions: "Today?" },
  },
};

const YAML = "apiVersion: choregos/v1\nkind: Workflow\n";
const DOCUMENT = {
  apiVersion: "choregos/v1",
  kind: "Workflow",
  metadata: { name: "support", version: 1 },
  actors: { router: ROUTER, owner: { type: "human", group: "support", sla_hours: 24 } },
  states: { inbox: { display: "Inbox" }, review: { display: "Review" }, done: { display: "Done", terminal: true } },
  transitions: [
    { id: "t-route", from: "inbox", to: "review", by: "router" },
    { id: "t-close", from: "review", to: "done", by: "owner" },
  ],
};
const edited = (): WorkflowEditResult => ({
  valid: true,
  errors: [],
  warnings: [],
  graph: { nodes: [], edges: [] },
  process: [],
  yaml: "after",
  diff: "+",
  inverse: [],
  notices: [],
});

/** The map and the process view of DOCUMENT, as `dsl/graph.py` and `dsl/process.py` render them. */
const GRAPH: WorkflowGraph = {
  nodes: [
    { id: "inbox", display: "Inbox", kind: "normal", lane: "decision" },
    { id: "review", display: "Review", kind: "normal", lane: "human" },
    { id: "done", display: "Done", kind: "normal", terminal: true, lane: "terminal" },
  ],
  edges: [
    { id: "t-route", from: "inbox", to: "review", kind: "nominal", actor: "router", actor_type: "decision", role: "route_ticket", gates: [] },
    { id: "t-close", from: "review", to: "done", kind: "nominal", actor: "owner", actor_type: "human", role: null, gates: [] },
  ],
};
const STEP: ProcessStep = {
  id: "t-route",
  from: "inbox",
  from_display: "Inbox",
  to: "review",
  to_display: "Review",
  actor: "router",
  actor_type: "decision",
  who: "a decision by `platform/decide` on title, description: route (choice of billing, tech), urgent (yes/no)",
  sentence: "From “Inbox”, a decision by `platform/decide` on title, description: route (choice of billing, tech), urgent (yes/no) moves the item to “Review”.",
  outputs: ["route", "route_confidence", "urgent", "urgent_confidence"],
  gates: [],
  role: "route_ticket",
  model: "platform/decide",
  inputs: [],
  output_types: {},
  writes_branch: false,
};

function YamlProbe() {
  return <p data-testid="yaml">{useDraft().yaml}</p>;
}

async function underDraft(content: ReactNode) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <DraftProvider slug="support" name="support">
        <YamlProbe />
        {content}
      </DraftProvider>
    </QueryClientProvider>,
  );
  await waitFor(() => expect(screen.getByTestId("yaml")).toHaveTextContent("apiVersion"));
}

beforeEach(() => {
  vi.mocked(api.workflowNamed).mockResolvedValue({ name: "support", version: 1, yaml: YAML, is_active: true, json: DOCUMENT } as never);
  vi.mocked(api.validateWorkflow).mockResolvedValue({ valid: true, errors: [], warnings: [], graph: GRAPH, process: [STEP], document: DOCUMENT } as never);
  vi.mocked(api.editWorkflow).mockReset().mockResolvedValue(edited());
  vi.mocked(api.agents).mockResolvedValue([] as Agent[]);
  vi.mocked(api.agentCatalogue).mockResolvedValue([] as AgentCatalogueEntry[]);
});

describe("a decision step is told apart from the platform (ADR 0121)", () => {
  it("is described by its role, its model and how many questions it asks", () => {
    expect(describeActor(ROUTER)).toBe("decision `route_ticket` by platform/decide, 2 questions");
    expect(describeActor({ ...ROUTER, model: "platform/decide-large", questions: { urgent: { type: "yes_no", instructions: "Today?" } } })).toBe(
      "decision `route_ticket` by platform/decide-large, 1 question",
    );
    expect(describeActor({ type: "system" })).toBe("the platform");
    expect(actorLabel("router", ROUTER)).toBe("router — decision · route_ticket");
  });

  it("finds the decision a step names in the workflow, and the outputs a `when` compares", () => {
    const workflow = DOCUMENT as unknown as WorkflowDocument;
    expect(decisionActorOf(workflow, "router")).toEqual(ROUTER);
    expect(decisionActorOf(workflow, "owner")).toBeUndefined();
    expect(decisionActorOf(workflow, undefined)).toBeUndefined();
    expect(decisionOutputs(ROUTER)).toEqual(["route", "route_confidence", "urgent", "urgent_confidence"]);
  });

  it("has its own lane on the map: its word, its icon and the categorical teal", () => {
    expect(actorKind("decision")).toBe("decision");
    expect(KIND_LABEL.decision).toBe("decision");
    expect(KIND_COLOURS.decision).toEqual({
      bord: `var(--choregos-cat-${DECISION_HUE}-ink)`,
      fond: `var(--choregos-cat-${DECISION_HUE}-soft)`,
      text: `var(--choregos-cat-${DECISION_HUE}-ink)`,
    });
    render(<WorkflowMap graph={GRAPH} initial="inbox" actors={{ router: { type: "decision", role: "route_ticket" }, owner: { type: "human", group: "support" } }} />);
    const box = screen.getByTestId("etat-inbox");
    expect(box.closest("[data-genre]")).toHaveAttribute("data-genre", "decision");
    expect(box).toHaveTextContent("Decision · router");
    expect(box).toHaveTextContent("route_ticket");
    expect(within(box).getByTestId("decision-inbox")).toBeInTheDocument();
    expect(screen.getByTestId("etat-review").closest("[data-genre]")).toHaveAttribute("data-genre", "human");
    expect(screen.getByRole("list", { name: "Who moves the work item" })).toHaveTextContent("Decision · 1");
  });

  it("is a teal diamond on the diagram, named in the legend only where the workflow has one", () => {
    const { unmount } = render(<SnakeMap graph={GRAPH} process={[STEP]} initial="inbox" />);
    const diagram = screen.getByTestId("carte-en-serpentin");
    const shape = diagram.querySelector('[data-etape="t-route"] .parcours-forme')!;
    expect(shape.tagName.toLowerCase()).toBe("polygon");
    expect(shape.getAttribute("stroke")).toBe(KIND_COLOURS.decision.border);
    expect(screen.getByTestId("etape-t-route")).toHaveAccessibleName(/^Router\s*decides: route ticket/);
    expect(screen.getByTestId("etape-t-route")).toHaveAccessibleDescription("decision — 0 attempts");
    expect(screen.getByRole("list", { name: "Who carries each step" })).toHaveTextContent("Decision");
    unmount();
    const { graph, process, initial } = journeyInProgress(Date.parse("2026-10-08T12:00:00Z"));
    render(<SnakeMap graph={graph} process={process} initial={initial} />);
    expect(screen.getByRole("list", { name: "Who carries each step" })).not.toHaveTextContent("Decision");
  });
});

describe("the step card of a decision (ADR 0121)", () => {
  it("shows its model and timeout, what it sends, its questions, and the outputs a `when` compares", () => {
    render(<StepDetails step={STEP} stepsById={new Map([[STEP.id, STEP]])} decision={ROUTER} />);
    expect(screen.getByText("Decision")).toBeInTheDocument();
    expect(screen.getByText("role route_ticket")).toBeInTheDocument();
    expect(screen.getByText("Model").nextElementSibling).toHaveTextContent(
      "platform/decide — answers within 15 s, through the project's gateway",
    );
    const sends = screen.getByTestId("decision-state-t-route");
    expect(within(sends).getByText("title")).toBeInTheDocument();
    expect(within(sends).getByText("description")).toBeInTheDocument();
    expect(within(screen.getByTestId("decision-questions-t-route")).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "route (choice: one of billing, tech) — Which team?",
      "urgent (yes/no: true or false) — Today?",
    ]);
    const produces = screen.getByText("Produces").nextElementSibling as HTMLElement;
    expect(within(produces).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "route — the answer, one of billing, tech",
      "route_confidence — its probability, from 0 to 1",
      "urgent — the answer, true or false",
      "urgent_confidence — its probability, from 0 to 1",
    ]);
    expect(screen.getByTestId("decision-when-t-route")).toHaveTextContent("when: { route: billing, route_confidence: { gte: 0.85 } }");
    expect(screen.getByTestId("decision-when-t-route")).toHaveTextContent("a threshold on it is a guess");
  });

  it("says the outputs from the workflow when the process view lists none, and a score's levels", () => {
    const scored: WorkflowDecisionActor = {
      ...ROUTER,
      timeout_seconds: 30,
      questions: { severity: { type: "score", instructions: "How bad?", levels: ["low", "high"] } },
    };
    render(<StepDetails step={{ ...STEP, outputs: [] }} stepsById={new Map()} decision={scored} />);
    expect(screen.getByText("Model").nextElementSibling).toHaveTextContent("answers within 30 s");
    const produces = screen.getByText("Produces").nextElementSibling as HTMLElement;
    expect(within(produces).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "severity — the answer, one of low, high, lowest first",
      "severity_confidence — its probability, from 0 to 1",
    ]);
    expect(screen.getByTestId("decision-when-t-route")).toHaveTextContent("when: { severity: high, severity_confidence: { gte: 0.85 } }");
  });
});

describe("what a decision may be (the contract, said before the engine says it)", () => {
  const valid: DecisionDraft = {
    role: "route_ticket",
    model: "platform/decide",
    state: ["title"],
    questions: [{ name: "route", type: "choice", instructions: "Which team?", values: "billing, tech" }],
    timeout: "",
  };

  it("accepts a complete draft and refuses what the contract refuses", () => {
    expect(decisionProblems(valid)).toEqual([]);
    expect(decisionProblems({ ...valid, role: "R" })).toHaveLength(1);
    expect(decisionProblems({ ...valid, state: [] })).toEqual(["Name at least one source the model reads."]);
    expect(decisionProblems({ ...valid, questions: [] })).toEqual(["Ask at least one question."]);
    expect(decisionProblems({ ...valid, questions: [{ ...valid.questions[0]!, values: "billing" }] })).toEqual([
      "Question 1: a choice offers 2 to 100 options.",
    ]);
    expect(
      decisionProblems({ ...valid, questions: [{ ...valid.questions[0]!, type: "score", values: "1,2,3,4,5,6,7,8,9,10,11" }] }),
    ).toEqual(["Question 1: a score has 2 to 10 levels."]);
    expect(decisionProblems({ ...valid, questions: [{ ...valid.questions[0]!, values: "tech, tech" }] })).toEqual([
      "Question 1: each option is named once.",
    ]);
    const clash = { name: "route_confidence", type: "yes_no" as const, instructions: "Sure?", values: "" };
    expect(decisionProblems({ ...valid, questions: [...valid.questions, clash] })).toHaveLength(2);
    expect(decisionProblems({ ...valid, timeout: "121" })).toEqual(["The time to answer is a whole number of seconds, 1 to 120."]);
  });

  it("writes only the fields that changed, a default left alone stays unwritten, a cleared timeout is removed", () => {
    expect(decisionEdits("router", ROUTER, draftOf(ROUTER))).toEqual([]);
    expect(draftOf(ROUTER).model).toBe("platform/decide");
    expect(decisionEdits("router", ROUTER, { ...draftOf(ROUTER), role: "route", timeout: "20" })).toEqual([
      { op: "set_actor", name: "router", field: "role", value: "route", style: "flow" },
      { op: "set_actor", name: "router", field: "timeout_seconds", value: 20, style: "flow" },
    ]);
    expect(decisionEdits("router", { ...ROUTER, timeout_seconds: 20 }, draftOf(ROUTER))).toEqual([
      { op: "set_actor", name: "router", field: "timeout_seconds", unset: true },
    ]);
  });
});

describe("creating and editing a decision on the map (ADR 0112, ADR 0121)", () => {
  it("adds a decision actor with its sources and questions, as one typed operation", async () => {
    await underDraft(<ActorsPanel org="acme" />);
    const panel = screen.getByTestId("actors-panel");
    expect(within(panel).getByRole("list", { name: "Actors of the workflow" })).toHaveTextContent(
      "router — decision `route_ticket` by platform/decide, 2 questions",
    );
    const form = within(panel).getByRole("form", { name: "Add an actor" });
    fireEvent.change(within(form).getByLabelText("Add"), { target: { value: "decision" } });
    expect(within(form).getByLabelText("Model (a catalogue entry with the decide usage)")).toHaveValue("platform/decide");
    fireEvent.change(within(form).getByLabelText("Role"), { target: { value: "triage" } });
    expect(within(form).getByLabelText("Name in the workflow")).toHaveValue("triage");

    // The sources: chips, the work item's own in one click, a field or an output checked against the contract.
    fireEvent.click(within(form).getByRole("button", { name: "Remove the source description" }));
    const source = within(form).getByLabelText("A field or an output (fields.name, outputs.name)");
    fireEvent.change(source, { target: { value: "repository" } });
    expect(within(form).getByRole("button", { name: "Add the source" })).toBeDisabled();
    expect(within(form).getByText(/Only title, description, labels, type/)).toBeInTheDocument();
    fireEvent.change(source, { target: { value: "fields.customer" } });
    fireEvent.click(within(form).getByRole("button", { name: "Add the source" }));
    expect(within(form).getByRole("list", { name: "Sources sent to the model" })).toHaveTextContent("fields.customer");

    // The questions: a choice with its options, then a yes/no.
    expect(within(form).getByRole("button", { name: "Add the actor" })).toBeDisabled();
    fireEvent.change(within(form).getByLabelText("Question 1 name"), { target: { value: "route" } });
    fireEvent.change(within(form).getByLabelText("Question 1 type"), { target: { value: "choice" } });
    fireEvent.change(within(form).getByLabelText("Question 1 instructions"), { target: { value: "Which team?" } });
    fireEvent.change(within(form).getByLabelText("Question 1 options (comma-separated)"), { target: { value: "billing, tech" } });
    expect(within(form).getByText("route_confidence")).toBeInTheDocument();
    fireEvent.click(within(form).getByRole("button", { name: "Add a question" }));
    fireEvent.change(within(form).getByLabelText("Question 2 name"), { target: { value: "urgent" } });
    fireEvent.change(within(form).getByLabelText("Question 2 instructions"), { target: { value: "Today?" } });
    fireEvent.change(within(form).getByLabelText("Answers within (seconds, 15 when empty)"), { target: { value: "10" } });

    fireEvent.click(within(form).getByRole("button", { name: "Add the actor" }));
    await waitFor(() =>
      expect(api.editWorkflow).toHaveBeenCalledWith(YAML, [
        {
          op: "add_actor",
          name: "triage",
          spec: {
            type: "decision",
            role: "triage",
            model: "platform/decide",
            state: ["title", "fields.customer"],
            questions: {
              route: { type: "choice", instructions: "Which team?", options: ["billing", "tech"] },
              urgent: { type: "yes_no", instructions: "Today?" },
            },
            timeout_seconds: 10,
          },
        },
      ]),
    );
  });

  it("edits a decision already in the workflow: the questions as lines, the timeout on one line, one gesture", async () => {
    await underDraft(<ActorsPanel org="acme" />);
    expect(screen.queryByRole("button", { name: "Edit the actor owner" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Edit the actor router" }));
    const form = screen.getByRole("form", { name: "Edit the decision router" });
    expect(within(form).getByRole("button", { name: "Save the decision" })).toBeDisabled();
    expect(within(form).getByLabelText("Question 1 options (comma-separated)")).toHaveValue("billing, tech");
    fireEvent.change(within(form).getByLabelText("Question 1 options (comma-separated)"), { target: { value: "billing, tech, other" } });
    fireEvent.change(within(form).getByLabelText("Answers within (seconds, 15 when empty)"), { target: { value: "30" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save the decision" }));
    await waitFor(() =>
      expect(api.editWorkflow).toHaveBeenCalledWith(YAML, [
        {
          op: "set_actor",
          name: "router",
          field: "questions",
          style: "block",
          value: {
            route: { type: "choice", instructions: "Which team?", options: ["billing", "tech", "other"] },
            urgent: { type: "yes_no", instructions: "Today?" },
          },
        },
        { op: "set_actor", name: "router", field: "timeout_seconds", value: 30, style: "flow" },
      ]),
    );
    await waitFor(() => expect(screen.queryByRole("form", { name: "Edit the decision router" })).toBeNull());
  });
});

/** `route` measured on 42 observed decisions: 0.70 is the lowest threshold that keeps 90 % of them right. */
const MEASURED: QuestionCalibration = {
  question: "route",
  source: "decision_model",
  model: "platform/decide",
  model_version: "laya-0.4.21",
  count: 57,
  observed: 42,
  accuracy: 37 / 42,
  ece: 0.04,
  bins: Array.from({ length: 10 }, (_, index) => ({
    lower: index / 10,
    upper: (index + 1) / 10,
    count: index >= 5 ? 8 : 0,
    mean_probability: index >= 5 ? index / 10 + 0.05 : null,
    accuracy: index >= 5 ? 0.75 : null,
  })),
  thresholds: [
    { threshold: 0.6, count: 40, coverage: 40 / 42, accuracy: 35 / 40 },
    { threshold: 0.65, count: 40, coverage: 40 / 42, accuracy: 35 / 40 },
    { threshold: 0.7, count: 36, coverage: 36 / 42, accuracy: 34 / 36 },
  ],
  suggested_threshold: 0.7,
  suggestion_reason: null,
};
/** `urgent` on 12: too few for a suggestion. */
const TOO_FEW: QuestionCalibration = {
  question: "urgent",
  source: "decision_model",
  model: "platform/decide",
  model_version: "laya-0.4.21",
  count: 15,
  observed: 12,
  accuracy: 10 / 12,
  ece: 0.09,
  bins: [],
  thresholds: [],
  suggested_threshold: null,
  suggestion_reason: "12 observed decision(s): a threshold is suggested from 30 on",
};
const calibrationOf = (questions: QuestionCalibration[]): StepCalibration => ({
  workflow: "support",
  step: "inbox",
  target_accuracy: 0.9,
  min_observed: 30,
  questions,
});

function withQueries(content: ReactNode) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{content}</QueryClientProvider>,
  );
}

const SOURCE = { project: "support", workflow: "support", thresholds: { route: 0.6 } };

describe("the calibration of a decision step (ADR 0121 §7, S52-06b)", () => {
  // A block, not an expression: a function returned by `beforeEach` is a teardown vitest calls after the test.
  beforeEach(() => {
    vi.mocked(api.stepCalibration).mockReset();
  });

  it("reads the threshold the workflow's `when` sets on each probability, from the exits of the state the decision leads to", () => {
    const workflow = {
      ...DOCUMENT,
      transitions: [
        { id: "t-route", from: "inbox", to: "review", by: "router" },
        { id: "t-billing", from: "review", to: "done", by: "owner", when: { route: "billing", route_confidence: { gte: 0.85 } } },
        { id: "t-tech", from: "review", to: "done", by: "owner", when: { route: "tech", route_confidence: { gt: 0.6 } } },
        { id: "t-calm", from: "review", to: "done", by: "owner", when: { urgent_confidence: { lte: 0.3 } } },
        { id: "t-other", from: "inbox", to: "done", by: "owner", when: { urgent_confidence: { gte: 0.5 } } },
        { id: "t-close", from: "review", to: "done", by: "owner" },
      ],
    } as unknown as WorkflowDocument;
    expect(workflowThresholds(workflow, "review")).toEqual({ route: 0.6 });
    expect(workflowThresholds(workflow, "inbox")).toEqual({ urgent: 0.5 });
    expect(workflowThresholds(undefined, "review")).toEqual({});
  });

  it("shows what the ledger measured, what the workflow's threshold keeps, and says it is below the suggested one", async () => {
    vi.mocked(api.stepCalibration).mockResolvedValue(calibrationOf([MEASURED, TOO_FEW]));
    withQueries(<StepDetails step={STEP} stepsById={new Map([[STEP.id, STEP]])} decision={ROUTER} calibration={SOURCE} />);
    await waitFor(() => expect(screen.getByTestId("decision-calibration")).toBeInTheDocument());
    expect(api.stepCalibration).toHaveBeenCalledWith("support", "support", "inbox", 0.9);

    const route = screen.getByRole("region", { name: "Calibration of route, answered by platform/decide (laya-0.4.21)" });
    expect(within(route).getByTestId("calibration-numbers-route")).toHaveTextContent(
      "42 of 57 decisions observed · 88 % of them right · expected calibration error 0.04",
    );
    expect(within(route).getByTestId("suggested-threshold-route")).toHaveTextContent("Suggested threshold for 90 % accuracy: 0.70");
    expect(within(route).getByTestId("workflow-threshold-route")).toHaveTextContent(
      "The workflow lets an answer through from route_confidence ≥ 0.60: 95 % of the observed decisions reach it (40), 88 % of them right.",
    );
    expect(within(route).getByTestId("threshold-below-route")).toHaveTextContent(
      "The workflow's threshold, 0.60, is below the suggested 0.70",
    );
    const reliability = within(route).getByRole("table", { name: "Reliability of route" });
    const rows = within(reliability).getAllByRole("row");
    expect(rows).toHaveLength(11);
    expect(within(rows[10]!).getAllByRole("cell").map((cell) => cell.textContent)).toEqual(["0.9–1.0", "8", "95 %", "75 %"]);
    expect(within(rows[1]!).getAllByRole("cell").map((cell) => cell.textContent)).toEqual(["0.0–0.1", "0", "—", "—"]);

    // The question with too few observed decisions: the reason, no suggestion, no comparison.
    const urgent = screen.getByRole("region", { name: /^Calibration of urgent/ });
    expect(within(urgent).getByTestId("no-suggestion-urgent")).toHaveTextContent(
      "No threshold suggested: 12 observed decisions; 30 are needed.",
    );
    expect(within(urgent).queryByTestId("suggested-threshold-urgent")).toBeNull();
    expect(within(urgent).queryByTestId("threshold-below-urgent")).toBeNull();
    expect(urgent).toHaveTextContent("The workflow's when sets no threshold on urgent_confidence.");

    // Measured: the probability is no longer called a guess.
    expect(screen.getByTestId("decision-when-t-route")).toHaveTextContent("measured against what happened next");
    expect(screen.getByTestId("decision-when-t-route")).not.toHaveTextContent("a threshold on it is a guess");
  });

  it("does not warn when the workflow's threshold is at or above the suggested one", async () => {
    vi.mocked(api.stepCalibration).mockResolvedValue(calibrationOf([MEASURED]));
    withQueries(
      <StepDetails step={STEP} stepsById={new Map()} decision={ROUTER} calibration={{ ...SOURCE, thresholds: { route: 0.7 } }} />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("workflow-threshold-route")).toHaveTextContent(
        "≥ 0.70: 86 % of the observed decisions reach it (36), 94 % of them right.",
      ),
    );
    expect(screen.queryByTestId("threshold-below-route")).toBeNull();
  });

  it("says why there is no suggestion, and keeps the probability a guess, while nothing is observed", async () => {
    vi.mocked(api.stepCalibration).mockResolvedValue(
      calibrationOf([{ ...TOO_FEW, count: 4, observed: 0, accuracy: null, ece: null, suggestion_reason: "0 observed decision(s)" }]),
    );
    withQueries(<StepDetails step={STEP} stepsById={new Map()} decision={ROUTER} calibration={SOURCE} />);
    await waitFor(() => expect(screen.getByTestId("no-suggestion-urgent")).toHaveTextContent("0 observed decisions; 30 are needed"));
    expect(screen.getByTestId("calibration-numbers-urgent")).toHaveTextContent(/^0 of 4 decisions observed$/);
    expect(screen.queryByRole("table", { name: "Reliability of urgent" })).toBeNull();
    expect(screen.getByTestId("decision-when-t-route")).toHaveTextContent("a threshold on it is a guess");
  });

  it("says when no decision is recorded yet", async () => {
    vi.mocked(api.stepCalibration).mockResolvedValue(calibrationOf([]));
    withQueries(<StepDetails step={STEP} stepsById={new Map()} decision={ROUTER} calibration={SOURCE} />);
    await waitFor(() => expect(screen.getByTestId("calibration-empty")).toHaveTextContent("No decision recorded yet"));
    expect(screen.getByTestId("decision-when-t-route")).toHaveTextContent("a threshold on it is a guess");
  });

  it("says while it reads", () => {
    vi.mocked(api.stepCalibration).mockReturnValue(new Promise<StepCalibration>(() => {}));
    withQueries(<StepDetails step={STEP} stepsById={new Map()} decision={ROUTER} calibration={SOURCE} />);
    expect(screen.getByRole("status")).toHaveTextContent("Reading the calibration…");
    expect(screen.getByTestId("decision-when-t-route")).toHaveTextContent("a threshold on it is a guess");
  });

  it("says the calibration could not be read, and the probability stays a guess", async () => {
    vi.mocked(api.stepCalibration).mockImplementation(() => Promise.reject(new Error("the ledger is unavailable")));
    withQueries(<StepDetails step={STEP} stepsById={new Map()} decision={ROUTER} calibration={SOURCE} />);
    expect(await screen.findByText(/Could not read the calibration: the ledger is unavailable/)).toBeInTheDocument();
    expect(api.stepCalibration).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.getByTestId("decision-when-t-route")).toHaveTextContent("a threshold on it is a guess");
    expect(screen.queryByTestId("decision-calibration")).toBeNull();
  });

  it("shows no calibration for a step that is not a decision", () => {
    withQueries(<StepDetails step={{ ...STEP, actor_type: "human", actor: "owner" }} stepsById={new Map()} calibration={SOURCE} />);
    expect(screen.queryByText("Calibration")).toBeNull();
    expect(api.stepCalibration).not.toHaveBeenCalled();
  });
});
