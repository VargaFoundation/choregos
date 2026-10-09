import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { BarreDuBrouillon } from "@/components/workflows/barre-du-brouillon";
import { BrouillonProvider, useBrouillon } from "@/components/workflows/brouillon";
import { operations } from "@/components/workflows/operations";
import { FormulaireDEtape, PanneauDEtat, PanneauDeTransition } from "@/components/workflows/panneaux";
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
 * Chaque geste de la console émet l'opération que le cœur greffe (S16-11). Le même fichier est lu
 * par `packages/core/tests/test_edition.py` : un champ renommé d'un côté fait rougir l'autre.
 */
describe("les gestes de la console", () => {
  type Geste = keyof typeof operations;
  it.each(fixture.gestes.map((g) => [g.geste, g.args, g.operation] as const))(
    "%s(%j) émet l'opération attendue",
    (geste, args, operation) => {
      const construire = operations[geste as Geste] as (...valeurs: unknown[]) => WorkflowOperation;
      expect(construire(...args)).toEqual(operation);
    },
  );

  it("chaque geste de la console est couvert par le fichier partagé", () => {
    expect(new Set(fixture.gestes.map((g) => g.geste))).toEqual(new Set(Object.keys(operations)));
  });
});

const YAML = fixture.workflow;
const graphe = {
  nodes: [
    { id: "inbox", display: "À trier", kind: "wait", lane: "agent" },
    { id: "ready", display: "Prêt", kind: "normal", lane: "agent" },
    { id: "done", display: "Fini", kind: "normal", terminal: true, lane: "terminal" },
  ],
  edges: [{ id: "t-implement", from: "ready", to: "done", kind: "nominal", actor: "dev", gates: ["scope_respected"] }],
};

function resultat(yaml: string, inverse: WorkflowOperation[]): WorkflowEditResult {
  return { valid: true, errors: [], warnings: [], graph: graphe, process: [], yaml, diff: `+${yaml}`, inverse, notices: [] };
}

/** Une sonde : le texte du brouillon, et un geste à la demande. */
function Sonde({ geste }: { geste?: WorkflowOperation }) {
  const brouillon = useBrouillon();
  return (
    <div>
      <p data-testid="yaml">{brouillon.yaml}</p>
      {geste && (
        <button type="button" onClick={() => void brouillon.appliquer(geste)}>
          geste
        </button>
      )}
    </div>
  );
}

/** Rend sous un brouillon, une fois le workflow lu : c'est là que la carte ouvre ses panneaux. */
async function rendre(contenu: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <BrouillonProvider slug="rh" name="onboarding">
        <BarreDuBrouillon />
        {contenu}
      </BrouillonProvider>
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
  vi.mocked(api.validateWorkflow).mockResolvedValue({ valid: true, errors: [], warnings: [], graph: graphe, process: [] });
  vi.mocked(api.editWorkflow).mockReset();
  vi.mocked(api.putWorkflowNamed).mockReset();
});

describe("le brouillon d'un workflow", () => {
  it("un geste part du texte lu ; annuler rejoue l'inverse du DERNIER geste, puis du précédent", async () => {
    const geste = operations.libelle("inbox", "Nouvelles demandes");
    const inverseA = [operations.libelle("inbox", "À trier")];
    const inverseB = [operations.libelle("inbox", "Nouvelles demandes")];
    vi.mocked(api.editWorkflow)
      .mockResolvedValueOnce(resultat("apres-A", inverseA))
      .mockResolvedValueOnce(resultat("apres-B", inverseB))
      .mockResolvedValueOnce(resultat("apres-A", [geste]))
      .mockResolvedValueOnce(resultat(YAML, [geste]));
    await rendre(<Sonde geste={geste} />);

    fireEvent.click(screen.getByRole("button", { name: "geste" }));
    await screen.findByText("1 change not published yet");
    expect(api.editWorkflow).toHaveBeenNthCalledWith(1, YAML, [geste]);
    fireEvent.click(screen.getByRole("button", { name: "geste" }));
    await screen.findByText("2 changes not published yet");
    // Le second geste se greffe sur le texte déjà édité, pas sur celui qui a été lu.
    expect(api.editWorkflow).toHaveBeenNthCalledWith(2, "apres-A", [geste]);
    expect(screen.getByTestId("yaml")).toHaveTextContent("apres-B");

    fireEvent.click(screen.getByRole("button", { name: "undo" }));
    await screen.findByText("1 change not published yet");
    expect(api.editWorkflow).toHaveBeenNthCalledWith(3, "apres-B", inverseB);
    fireEvent.click(screen.getByRole("button", { name: "undo" }));
    await waitFor(() => expect(screen.queryByTestId("brouillon")).toBeNull());
    expect(api.editWorkflow).toHaveBeenNthCalledWith(4, "apres-A", inverseA);
  });

  it("avant que le workflow soit lu, un geste ne part pas sur un texte vide", async () => {
    vi.mocked(api.workflowNamed).mockReturnValue(new Promise(() => {}));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <BrouillonProvider slug="rh" name="onboarding">
          <Sonde geste={operations.retirerLEtat("x")} />
        </BrouillonProvider>
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "geste" }));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(api.editWorkflow).not.toHaveBeenCalled();
  });

  it("publier envoie le texte avec la version lue ; un 409 le dit sans rien écraser", async () => {
    vi.mocked(api.editWorkflow).mockResolvedValue(resultat("editee", []));
    vi.mocked(api.putWorkflowNamed).mockRejectedValue(new ApiError(409, { title: "Conflict" }));
    await rendre(<Sonde geste={operations.genre("ready", "wait")} />);
    fireEvent.click(screen.getByRole("button", { name: "geste" }));

    fireEvent.click(await screen.findByRole("button", { name: "publish v4" }));
    await screen.findByText(/someone published a newer version meanwhile/);
    expect(api.putWorkflowNamed).toHaveBeenCalledWith("rh", "onboarding", "editee", 3);
    // Le brouillon reste : rien n'est perdu, l'auteur choisit d'abandonner.
    expect(screen.getByText("1 change not published yet")).toBeInTheDocument();
  });

  it("un geste refusé par l'API laisse le texte tel quel et dit pourquoi", async () => {
    vi.mocked(api.editWorkflow).mockRejectedValue(new ApiError(422, { title: "Refusé", detail: "état inconnu : x" }));
    await rendre(<Sonde geste={operations.retirerLEtat("x")} />);
    fireEvent.click(screen.getByRole("button", { name: "geste" }));
    await screen.findByText("état inconnu : x");
    expect(screen.getByTestId("yaml")).toHaveTextContent("apiVersion");
  });
});

describe("les panneaux de la carte et de la vue processus", () => {
  const etats = graphe.nodes;

  it("le panneau d'un état émet le libellé, le renommage et la transition saisis", async () => {
    vi.mocked(api.editWorkflow).mockResolvedValue(resultat(YAML, []));
    await rendre(
      <>
        <Sonde />
        <PanneauDEtat noeud={etats[0]!} etats={etats} acteurs={["refiner", "owner"]} />
      </>,
    );
    const panneau = screen.getByTestId("panneau-etat");

    fireEvent.change(screen.getByLabelText("label"), { target: { value: "Nouvelles demandes" } });
    fireEvent.click(screen.getByRole("button", { name: "set label" }));
    await waitFor(() =>
      expect(api.editWorkflow).toHaveBeenLastCalledWith(YAML, [operations.libelle("inbox", "Nouvelles demandes")]),
    );

    fireEvent.change(screen.getByLabelText("name (every reference follows)"), { target: { value: "triage" } });
    fireEvent.click(screen.getByRole("button", { name: "rename" }));
    await waitFor(() => expect(api.editWorkflow).toHaveBeenLastCalledWith(YAML, [operations.renommer("inbox", "triage")]));

    fireEvent.change(screen.getByLabelText("new transition to"), { target: { value: "ready" } });
    fireEvent.change(screen.getByLabelText("by"), { target: { value: "owner" } });
    fireEvent.click(screen.getByRole("button", { name: "add" }));
    await waitFor(() =>
      expect(api.editWorkflow).toHaveBeenLastCalledWith(YAML, [operations.ajouterUneTransition("inbox", "ready", "owner")]),
    );

    // Un nouvel état et la transition qui y mène partent en un geste : un seul « undo » les défait.
    fireEvent.change(screen.getByLabelText("new transition to"), { target: { value: "(new)" } });
    fireEvent.change(screen.getByLabelText("name of the new state"), { target: { value: "verification" } });
    fireEvent.click(screen.getByRole("button", { name: "add" }));
    await waitFor(() =>
      expect(api.editWorkflow).toHaveBeenLastCalledWith(YAML, [
        operations.ajouterUnEtat("verification", "verification"),
        operations.ajouterUneTransition("inbox", "verification", "owner"),
      ]),
    );
    expect(panneau).toBeInTheDocument();
  });

  it("une étape s'ajoute sans choisir d'état d'abord : on dit d'où elle part (S23-13)", async () => {
    vi.mocked(api.editWorkflow).mockResolvedValue(resultat(YAML, []));
    await rendre(
      <>
        <Sonde />
        <FormulaireDEtape etats={etats} acteurs={["refiner", "owner"]} />
      </>,
    );
    const ajouter = screen.getByRole("button", { name: "add" });
    expect(ajouter).toBeDisabled();
    fireEvent.change(screen.getByLabelText("from"), { target: { value: "inbox" } });
    fireEvent.change(screen.getByLabelText("to"), { target: { value: "(new)" } });
    fireEvent.change(screen.getByLabelText("name of the new state"), { target: { value: "in_review" } });
    fireEvent.click(ajouter);
    await waitFor(() =>
      expect(api.editWorkflow).toHaveBeenLastCalledWith(YAML, [
        operations.ajouterUnEtat("in_review", "in_review"),
        operations.ajouterUneTransition("inbox", "in_review", "refiner"),
      ]),
    );
  });

  it("le panneau d'une transition émet l'acteur, la garantie, le délai et leur retrait", async () => {
    vi.mocked(api.editWorkflow).mockResolvedValue(resultat(YAML, []));
    await rendre(
      <>
        <Sonde />
        <PanneauDeTransition arete={{ ...graphe.edges[0]!, timeout_hours: 72 }} acteurs={["dev", "owner"]} />
      </>,
    );

    fireEvent.change(screen.getByLabelText("who moves it"), { target: { value: "owner" } });
    fireEvent.click(screen.getAllByRole("button", { name: "set" })[0]!);
    await waitFor(() => expect(api.editWorkflow).toHaveBeenLastCalledWith(YAML, [operations.acteur("t-implement", "owner")]));

    fireEvent.click(screen.getByRole("button", { name: "remove the guarantee scope_respected" }));
    await waitFor(() =>
      expect(api.editWorkflow).toHaveBeenLastCalledWith(YAML, [operations.retirerUneGarantie("t-implement", "scope_respected")]),
    );

    fireEvent.change(screen.getByLabelText("guarantee to add"), { target: { value: "ci_green" } });
    fireEvent.click(screen.getByRole("button", { name: "add" }));
    await waitFor(() =>
      expect(api.editWorkflow).toHaveBeenLastCalledWith(YAML, [operations.ajouterUneGarantie("t-implement", "ci_green")]),
    );

    // Le délai lu est prérempli ; vidé, il retire la limite.
    const delai = screen.getByLabelText("at most (hours, empty: no limit)");
    expect(delai).toHaveValue(72);
    fireEvent.change(delai, { target: { value: "" } });
    fireEvent.click(screen.getAllByRole("button", { name: "set" })[1]!);
    await waitFor(() => expect(api.editWorkflow).toHaveBeenLastCalledWith(YAML, [operations.delai("t-implement", null)]));
  });

  it("une flèche secondaire ne s'édite pas ici : le panneau renvoie au YAML", async () => {
    await rendre(
      <>
        <Sonde />
        <PanneauDeTransition arete={{ from: "ready", to: "inbox", kind: "reject" }} acteurs={[]} />
      </>,
    );
    expect(screen.getByText(/edit it in the YAML/)).toBeInTheDocument();
    expect(screen.queryByTestId("panneau-transition")).toBeNull();
  });
});
