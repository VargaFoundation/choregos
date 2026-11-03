import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CarteEnSerpentin } from "@/components/parcours/carte-du-parcours";
import { ActorsPanel } from "@/components/workflows/actors-panel";
import { BrouillonProvider, useBrouillon } from "@/components/workflows/brouillon";
import { CarteDuWorkflow } from "@/components/workflows/carte/carte-du-workflow";
import { COULEUR_DU_GENRE, DECISION_HUE, genreDActeur, LIBELLE_DU_GENRE } from "@/components/workflows/carte/disposition";
import { decisionEdits, decisionProblems, draftOf, type DecisionDraft } from "@/components/workflows/decision-actor-form";
import { decisionActorOf, decisionOutputs, describeActor, type WorkflowDecisionActor, type WorkflowDocument } from "@/components/workflows/document";
import { actorLabel } from "@/components/workflows/panneaux";
import { StepDetails } from "@/components/workflows/step-details";
import { api } from "@/lib/api";
import type { Agent, AgentCatalogueEntry, ProcessStep, WorkflowEditResult, WorkflowGraph } from "@/lib/types";
import { parcoursEnCours } from "@/mocks/parcours";

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
  return <p data-testid="yaml">{useBrouillon().yaml}</p>;
}

async function underDraft(content: ReactNode) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <BrouillonProvider slug="support" name="support">
        <YamlProbe />
        {content}
      </BrouillonProvider>
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
    expect(genreDActeur("decision")).toBe("decision");
    expect(LIBELLE_DU_GENRE.decision).toBe("decision");
    expect(COULEUR_DU_GENRE.decision).toEqual({
      bord: `var(--choregos-cat-${DECISION_HUE}-ink)`,
      fond: `var(--choregos-cat-${DECISION_HUE}-soft)`,
      texte: `var(--choregos-cat-${DECISION_HUE}-ink)`,
    });
    render(<CarteDuWorkflow graph={GRAPH} initial="inbox" acteurs={{ router: { type: "decision", role: "route_ticket" }, owner: { type: "human", group: "support" } }} />);
    const box = screen.getByTestId("etat-inbox");
    expect(box.closest("[data-genre]")).toHaveAttribute("data-genre", "decision");
    expect(box).toHaveTextContent("Decision · router");
    expect(box).toHaveTextContent("route_ticket");
    expect(within(box).getByTestId("decision-inbox")).toBeInTheDocument();
    expect(screen.getByTestId("etat-review").closest("[data-genre]")).toHaveAttribute("data-genre", "human");
    expect(screen.getByRole("list", { name: "Who moves the work item" })).toHaveTextContent("Decision · 1");
  });

  it("is a teal diamond on the diagram, named in the legend only where the workflow has one", () => {
    const { unmount } = render(<CarteEnSerpentin graph={GRAPH} process={[STEP]} initial="inbox" />);
    const carte = screen.getByTestId("carte-en-serpentin");
    const forme = carte.querySelector('[data-etape="t-route"] .parcours-forme')!;
    expect(forme.tagName.toLowerCase()).toBe("polygon");
    expect(forme.getAttribute("stroke")).toBe(COULEUR_DU_GENRE.decision.bord);
    expect(screen.getByTestId("etape-t-route")).toHaveAccessibleName(/^Router\s*decides: route ticket/);
    expect(screen.getByTestId("etape-t-route")).toHaveAccessibleDescription("decision — 0 attempts");
    expect(screen.getByRole("list", { name: "Who carries each step" })).toHaveTextContent("Decision");
    unmount();
    const { graph, process, initial } = parcoursEnCours(Date.parse("2026-10-08T12:00:00Z"));
    render(<CarteEnSerpentin graph={graph} process={process} initial={initial} />);
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
