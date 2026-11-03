import { describe, expect, it } from "vitest";
import { kindOfOutcome, toneOfState } from "@/components/ui";
import { boardWorkflows, columnsFromGraph, foldedItems, itemsOf, TERMINAL_FOLD, visibleColumns } from "@/components/workflows/board";
import { champsDepuisSchema, valeursPourLApi } from "@/components/workflows/champs";
import type { WorkflowGraph, WorkItemDto } from "@/lib/types";

const ticket = (id: string, state: string, workflow_name?: string) =>
  ({ id, state, workflow_name, title: id, tracker_key: id }) as unknown as WorkItemDto;

describe("a board's columns (ADR 0031, S16-10)", () => {
  // The validator's graph: the YAML's indentation no longer matters: a YAML indented by
  // four spaces gave zero columns to the former regex.
  const graph = {
    nodes: [
      { id: "arrivee", display: "Arrival", kind: "wait" },
      { id: "preparation", display: "Preparation", kind: "work" },
      { id: "fait", display: "Fait", kind: "terminal", terminal: true },
    ],
    edges: [],
  } as unknown as WorkflowGraph;

  it("come from the workflow's states, in its order, with their kind", () => {
    const columns = columnsFromGraph(graph, [ticket("a", "preparation")]);
    expect(columns.map((c) => [c.state, c.display, c.kind, c.items.length])).toEqual([
      ["arrivee", "Arrival", "wait", 0],
      ["preparation", "Preparation", undefined, 1],
      ["fait", "Fait", "terminal", 0],
    ]);
  });

  it("keep a ticket visible in a state that the workflow no longer knows", () => {
    const columns = columnsFromGraph(graph, [ticket("b", "ancien_etat")]);
    expect(columns.at(-1)?.state).toBe("ancien_etat");
    // And the column says so: it alone is outside the workflow (S23-04).
    expect(columns.filter((c) => c.horsWorkflow).map((c) => c.state)).toEqual(["ancien_etat"]);
  });

  it("show only the workflow's tickets; those from before the pin go to the default", () => {
    const tickets = [ticket("a", "x", "onboarding"), ticket("b", "x", "offboarding"), ticket("c", "x")];
    expect(itemsOf(tickets, "onboarding", true).map((t) => t.id)).toEqual(["a", "c"]);
    expect(itemsOf(tickets, "offboarding", false).map((t) => t.id)).toEqual(["b"]);
  });
});

describe("what a board shows of its columns (2026-11-02)", () => {
  const graph = {
    nodes: [
      { id: "inbox", display: "To take", kind: "wait" },
      { id: "diagnosed", display: "Diagnosed", kind: "work" },
      { id: "answered", display: "Answered", kind: "terminal", terminal: true },
    ],
    edges: [],
  } as unknown as WorkflowGraph;

  it("hides the empty columns unless asked, and says how many it hid", () => {
    const columns = columnsFromGraph(graph, [ticket("a", "diagnosed")]);
    expect(visibleColumns(columns, false)).toEqual({ shown: [columns[1]], hidden: 2 });
    expect(visibleColumns(columns, true)).toEqual({ shown: columns, hidden: 0 });
  });

  it("keeps every declared state, however long the workflow", () => {
    const long = {
      nodes: Array.from({ length: 14 }, (_, index) => ({ id: `s${index}`, display: `S${index}`, kind: "work" })),
      edges: [],
    } as unknown as WorkflowGraph;
    // The toggle decides now; columnsFromGraph used to drop empty states past twelve.
    expect(columnsFromGraph(long, [])).toHaveLength(14);
  });

  it("folds a long terminal column to its most recent tickets", () => {
    const done = Array.from({ length: 5 }, (_, index) => ({
      ...ticket(`d${index}`, "answered"),
      created_at: `2026-11-0${index + 1}T10:00:00Z`,
    })) as WorkItemDto[];
    const column = columnsFromGraph(graph, done)[2]!;
    const folded = foldedItems(column, false);
    expect(folded.folded).toBe(5 - TERMINAL_FOLD);
    expect(folded.items.map((item) => item.id)).toEqual(["d4", "d3", "d2"]);
    expect(foldedItems(column, true)).toEqual({ items: column.items, folded: 0 });
    // A live column is never folded.
    const live = columnsFromGraph(graph, done.map((item) => ({ ...item, state: "diagnosed" })))[1]!;
    expect(foldedItems(live, false).folded).toBe(0);
  });

  it("lists every workflow, the default first, or only the one asked for", () => {
    const list = [
      { name: "fon-access", is_default: false },
      { name: "fon-support", is_default: true },
    ];
    expect(boardWorkflows(list, null).map((w) => w.name)).toEqual(["fon-support", "fon-access"]);
    expect(boardWorkflows(list, "fon-access").map((w) => w.name)).toEqual(["fon-access"]);
    expect(boardWorkflows(list, "missing")).toEqual([]);
  });
});

describe("a request's fields, from `metadata.inputs`", () => {
  const schema = {
    type: "object",
    required: ["date_arrivee", "poste"],
    properties: {
      date_arrivee: { type: "string", format: "date", title: "arrival date" },
      poste: { type: "string", enum: ["dev", "rh", "commercial"] },
      jours_d_essai: { type: "integer" },
      teletravail: { type: "boolean" },
      materiel: { type: "array", items: { type: "string" }, description: "comma separated" },
      manager: { type: "string" },
    },
  };

  it("are derived from the schema: control, label, requirement, choices", () => {
    const fields = champsDepuisSchema(schema);
    expect(fields.map((c) => [c.name, c.control, c.required])).toEqual([
      ["date_arrivee", "date", true],
      ["poste", "choice", true],
      ["jours_d_essai", "integer", false],
      ["teletravail", "boolean", false],
      ["materiel", "list", false],
      ["manager", "text", false],
    ]);
    expect(fields[0]?.label).toBe("arrival date");
    expect(fields[1]?.choices).toEqual(["dev", "rh", "commercial"]);
    expect(champsDepuisSchema(undefined)).toEqual([]);
  });

  it("leave typed as the schema expects them; an empty field does not leave", () => {
    const fields = champsDepuisSchema(schema);
    expect(
      valeursPourLApi(fields, {
        date_arrivee: "2026-11-02",
        poste: "dev",
        jours_d_essai: "90",
        teletravail: true,
        materiel: "pc, badge , ",
        manager: "",
      }),
    ).toEqual({ date_arrivee: "2026-11-02", poste: "dev", jours_d_essai: 90, teletravail: true, materiel: ["pc", "badge"] });
  });
});

describe("a terminal state's declared outcome", () => {
  const graph = {
    nodes: [
      { id: "to_apply", display: "To apply", kind: "wait" },
      { id: "applied", display: "Applied", kind: "terminal", terminal: true, outcome: "success" },
      { id: "declined", display: "Declined", kind: "terminal", terminal: true, outcome: "declined" },
      { id: "handed_back", display: "Handed back", kind: "terminal", terminal: true, outcome: "cancelled" },
      // A graph from an API before the field: a terminal state is a success.
      { id: "done", display: "Done", kind: "terminal", terminal: true },
    ],
    edges: [],
  } as unknown as WorkflowGraph;

  it("gives its column its own kind: only a success is the green terminal", () => {
    const kinds = Object.fromEntries(columnsFromGraph(graph, []).map((c) => [c.state, c.kind]));
    expect(kinds).toEqual({
      to_apply: "wait",
      applied: "terminal",
      declined: "declined",
      handed_back: "cancelled",
      done: "terminal",
    });
  });

  it("folds a long declined column like a successful one: it is finished too", () => {
    const tickets = Array.from({ length: TERMINAL_FOLD + 2 }, (_, i) => ticket(`d${i}`, "declined"));
    const declined = columnsFromGraph(graph, tickets).find((c) => c.state === "declined");
    expect(declined && foldedItems(declined, false).folded).toBe(2);
  });

  it("is never shown in the tone of a success when the request was declined or cancelled", () => {
    expect(toneOfState("applied", kindOfOutcome("success"))).toBe("succeeded");
    expect(toneOfState("declined", kindOfOutcome("declined"))).toBe("danger");
    expect(toneOfState("handed_back", kindOfOutcome("cancelled"))).toBe("neutral");
    expect(kindOfOutcome(null)).toBe("terminal");
  });
});
