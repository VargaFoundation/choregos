import { describe, expect, it } from "vitest";
import { columnsFromGraph, itemsOf } from "@/components/workflows/board";
import { champsDepuisSchema, valeursPourLApi } from "@/components/workflows/champs";
import type { WorkflowGraph, WorkItemDto } from "@/lib/types";

const ticket = (id: string, state: string, workflow_name?: string) =>
  ({ id, state, workflow_name, title: id, tracker_key: id }) as unknown as WorkItemDto;

describe("les colonnes d'un board (ADR 0031, S16-10)", () => {
  // Le graphe du validateur : l'indentation du YAML n'y est plus pour rien — un YAML indenté de
  // quatre espaces donnait zéro colonne au regex d'avant.
  const graph = {
    nodes: [
      { id: "arrivee", display: "Arrivée", kind: "wait" },
      { id: "preparation", display: "Préparation", kind: "work" },
      { id: "fait", display: "Fait", kind: "terminal", terminal: true },
    ],
    edges: [],
  } as unknown as WorkflowGraph;

  it("viennent des états du workflow, dans son ordre, avec leur genre", () => {
    const columns = columnsFromGraph(graph, [ticket("a", "preparation")]);
    expect(columns.map((c) => [c.state, c.display, c.kind, c.items.length])).toEqual([
      ["arrivee", "Arrivée", "wait", 0],
      ["preparation", "Préparation", undefined, 1],
      ["fait", "Fait", "terminal", 0],
    ]);
  });

  it("gardent visible un ticket dans un état que le workflow ne connaît plus", () => {
    const columns = columnsFromGraph(graph, [ticket("b", "ancien_etat")]);
    expect(columns.at(-1)?.state).toBe("ancien_etat");
    // Et la colonne le dit : seule elle est hors du workflow (S23-04).
    expect(columns.filter((c) => c.horsWorkflow).map((c) => c.state)).toEqual(["ancien_etat"]);
  });

  it("ne montrent que les tickets du workflow ; ceux d'avant l'épingle vont au défaut", () => {
    const tickets = [ticket("a", "x", "onboarding"), ticket("b", "x", "offboarding"), ticket("c", "x")];
    expect(itemsOf(tickets, "onboarding", true).map((t) => t.id)).toEqual(["a", "c"]);
    expect(itemsOf(tickets, "offboarding", false).map((t) => t.id)).toEqual(["b"]);
  });
});

describe("les champs d'une demande, depuis `metadata.inputs`", () => {
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

  it("se déduisent du schéma : contrôle, libellé, obligation, choix", () => {
    const champs = champsDepuisSchema(schema);
    expect(champs.map((c) => [c.name, c.control, c.required])).toEqual([
      ["date_arrivee", "date", true],
      ["poste", "choice", true],
      ["jours_d_essai", "integer", false],
      ["teletravail", "boolean", false],
      ["materiel", "list", false],
      ["manager", "text", false],
    ]);
    expect(champs[0]?.label).toBe("arrival date");
    expect(champs[1]?.choices).toEqual(["dev", "rh", "commercial"]);
    expect(champsDepuisSchema(undefined)).toEqual([]);
  });

  it("partent typés comme le schéma les attend ; un champ vide ne part pas", () => {
    const champs = champsDepuisSchema(schema);
    expect(
      valeursPourLApi(champs, {
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
