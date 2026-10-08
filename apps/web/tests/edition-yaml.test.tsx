import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import YamlPage from "@/app/p/[slug]/workflows/[name]/yaml/page";
import { BarreDuBrouillon } from "@/components/workflows/barre-du-brouillon";
import { BrouillonProvider, useBrouillon } from "@/components/workflows/brouillon";
import { operations } from "@/components/workflows/operations";
import { ApiError, api } from "@/lib/api";
import type { WorkflowEditResult, WorkflowValidation } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
    api: { ...reel.api, workflowNamed: vi.fn(), validateWorkflow: vi.fn(), editWorkflow: vi.fn(), putWorkflowNamed: vi.fn() },
  };
});
// L'éditeur réel (CodeMirror) a ses propres tests : ici, un champ de texte suffit à taper.
vi.mock("next/dynamic", () => ({
  default: () =>
    function Editeur({ value, onChange, label }: { value: string; onChange: (v: string) => void; label: string }) {
      return <textarea aria-label={label} value={value} onChange={(e) => onChange(e.target.value)} />;
    },
}));
vi.mock("next/navigation", () => ({ usePathname: () => "/p/rh/workflows/onboarding/yaml" }));

const LU = "apiVersion: choregos/v1\nkind: Workflow\nmetadata: { name: onboarding, version: 3 }\nstates:\n  inbox: { display: Inbox }\n";
const graphe = { nodes: [{ id: "inbox", display: "Inbox", kind: "wait", lane: "agent" }], edges: [] };
const valide: WorkflowValidation = { valid: true, errors: [], warnings: [], graph: graphe, process: [] };
const invalide: WorkflowValidation = {
  valid: false,
  errors: [{ code: "workflow.states", message: "a workflow declares its states", line: 1, column: 1 }],
  warnings: [],
};
const version = { courante: 3 };

function Sonde() {
  const brouillon = useBrouillon();
  return (
    <div>
      <textarea aria-label="texte" value={brouillon.yaml} onChange={(e) => brouillon.ecrire(e.target.value)} />
      <p data-testid="validation">{brouillon.validation.etat}</p>
      <button type="button" onClick={() => void brouillon.appliquer(operations.libelle("inbox", "New requests"))}>
        geste
      </button>
    </div>
  );
}

/** Un onglet qu'on quitte et où l'on revient : le fournisseur du brouillon, lui, reste (il vit dans la mise en page). */
function Onglets({ children }: { children: ReactNode }) {
  const [visible, setVisible] = useState(true);
  return (
    <>
      <button type="button" onClick={() => setVisible(!visible)}>
        changer d&apos;onglet
      </button>
      {visible && children}
    </>
  );
}

let client: QueryClient;
async function rendre(contenu: ReactNode = <Sonde />) {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <BrouillonProvider slug="rh" name="onboarding">
        <BarreDuBrouillon />
        <Onglets>{contenu}</Onglets>
      </BrouillonProvider>
    </QueryClientProvider>,
  );
  await waitFor(() => expect((screen.getAllByRole("textbox")[0] as HTMLTextAreaElement).value).toContain("apiVersion"));
}

const taper = (texte: string) => fireEvent.change(screen.getByLabelText("texte"), { target: { value: texte } });
const valideAuBout = () => waitFor(() => expect(screen.getByTestId("validation")).toHaveTextContent("a_jour"), { timeout: 2000 });

beforeEach(() => {
  version.courante = 3;
  vi.mocked(api.workflowNamed).mockImplementation(
    async () => ({ name: "onboarding", version: version.courante, yaml: LU, is_active: true, json: { actors: {} } }) as never,
  );
  vi.mocked(api.validateWorkflow).mockReset().mockResolvedValue(valide);
  vi.mocked(api.editWorkflow).mockReset();
  vi.mocked(api.putWorkflowNamed).mockReset().mockResolvedValue({ name: "onboarding", version: 4 } as never);
});

describe("le brouillon unique des trois vues (S21-06)", () => {
  it("le texte tapé devient le brouillon, et se publie avec la version lue au début", async () => {
    await rendre();
    taper(`${LU}# typed\n`);
    expect(await screen.findByText("1 change not published yet")).toBeInTheDocument();
    await valideAuBout();
    fireEvent.click(screen.getByRole("button", { name: "publish v4" }));
    await waitFor(() => expect(api.putWorkflowNamed).toHaveBeenCalledWith("rh", "onboarding", `${LU}# typed\n`, 3));
  });

  it("le texte survit au changement d'onglet", async () => {
    await rendre();
    taper(`${LU}# kept\n`);
    fireEvent.click(screen.getByRole("button", { name: "changer d'onglet" }));
    expect(screen.queryByLabelText("texte")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "changer d'onglet" }));
    expect((screen.getByLabelText("texte") as HTMLTextAreaElement).value).toBe(`${LU}# kept\n`);
  });

  it("une validation périmée ne remplace pas la plus récente", async () => {
    let lente: (v: WorkflowValidation) => void = () => {};
    vi.mocked(api.validateWorkflow).mockImplementation((texte: string) =>
      texte === "kind: Workflow\n" ? new Promise((resolve) => (lente = resolve)) : Promise.resolve(valide),
    );
    await rendre();
    taper("kind: Workflow\n");
    await waitFor(() => expect(api.validateWorkflow).toHaveBeenCalledWith("kind: Workflow\n", expect.anything()), {
      timeout: 2000,
    });
    taper(`${LU}# fixed\n`);
    await valideAuBout();
    // La première réponse arrive APRÈS la seconde : elle ne doit rien écrire.
    act(() => lente(invalide));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText(/would be invalid/)).toBeNull();
    expect(screen.getByRole("button", { name: "publish v4" })).toBeEnabled();
  });

  it("une réponse périmée qui arrive pendant la validation suivante ne débloque pas la publication", async () => {
    const enAttente = new Map<string, (v: WorkflowValidation) => void>();
    vi.mocked(api.validateWorkflow).mockImplementation((texte: string) =>
      texte === LU ? Promise.resolve(valide) : new Promise((resolve) => enAttente.set(texte, resolve)),
    );
    await rendre();
    taper(`${LU}# A\n`);
    await waitFor(() => expect(enAttente.has(`${LU}# A\n`)).toBe(true), { timeout: 2000 });
    taper(`${LU}# B\n`);
    await waitFor(() => expect(enAttente.has(`${LU}# B\n`)).toBe(true), { timeout: 2000 });
    // A répond alors que B est encore en vol : le verdict de A ne vaut pas pour B.
    await act(async () => enAttente.get(`${LU}# A\n`)!(valide));
    expect(screen.getByTestId("validation")).toHaveTextContent("en_cours");
    expect(screen.getByRole("button", { name: "publish v4" })).toBeDisabled();
    await act(async () => enAttente.get(`${LU}# B\n`)!(valide));
    await valideAuBout();
    expect(screen.getByRole("button", { name: "publish v4" })).toBeEnabled();
  });

  it("une validation injoignable le dit et interdit de publier ; réessayer la relance", async () => {
    let appels = 0;
    vi.mocked(api.validateWorkflow).mockImplementation(async (texte: string) => {
      if (texte === LU) return valide; // la version publiée
      appels += 1;
      if (appels === 1) throw new Error("network down");
      return valide;
    });
    await rendre();
    taper(`${LU}# offline\n`);
    expect(await screen.findByText(/could not validate the text: network down/, {}, { timeout: 2000 })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "publish v4" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "retry" }));
    await valideAuBout();
    expect(screen.getByRole("button", { name: "publish v4" })).toBeEnabled();
  });

  it("un texte invalide interdit de publier", async () => {
    vi.mocked(api.validateWorkflow).mockResolvedValue(invalide);
    await rendre();
    taper("kind: Workflow\n");
    await valideAuBout();
    expect(screen.getByText(/the workflow would be invalid/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "publish v4" })).toBeDisabled();
  });

  it("une version publiée pendant l'édition ne remplace pas le texte et ouvre le conflit", async () => {
    await rendre();
    taper(`${LU}# mine\n`);
    await valideAuBout();
    version.courante = 4;
    await act(() => client.invalidateQueries({ queryKey: ["workflow", "rh", "onboarding"] }));
    expect(await screen.findByTestId("conflit")).toHaveTextContent("you started from v3, the workflow is now v4");
    expect((screen.getByLabelText("texte") as HTMLTextAreaElement).value).toBe(`${LU}# mine\n`);
    expect(screen.getByRole("button", { name: "publish v5" })).toBeDisabled();
  });

  it("un 409 garde le texte et propose de recharger ou d'écraser", async () => {
    vi.mocked(api.putWorkflowNamed)
      .mockRejectedValueOnce(new ApiError(409, { title: "Conflict" }))
      .mockResolvedValueOnce({ name: "onboarding", version: 5 } as never);
    await rendre();
    taper(`${LU}# mine\n`);
    await valideAuBout();
    version.courante = 4;
    fireEvent.click(screen.getByRole("button", { name: "publish v4" }));
    expect(await screen.findByTestId("conflit")).toBeInTheDocument();
    expect((screen.getByLabelText("texte") as HTMLTextAreaElement).value).toBe(`${LU}# mine\n`);
    fireEvent.click(screen.getByRole("button", { name: "publish over v4" }));
    await waitFor(() => expect(api.putWorkflowNamed).toHaveBeenLastCalledWith("rh", "onboarding", `${LU}# mine\n`, 4));
  });

  it("un geste sur la carte s'applique au texte édité", async () => {
    const resultat = { ...valide, yaml: `${LU}# typed\n# grafted\n`, diff: "+# grafted", inverse: [], notices: [] } as WorkflowEditResult;
    vi.mocked(api.editWorkflow).mockResolvedValue(resultat);
    await rendre();
    taper(`${LU}# typed\n`);
    fireEvent.click(screen.getByRole("button", { name: "geste" }));
    await waitFor(() => expect(api.editWorkflow).toHaveBeenCalledWith(`${LU}# typed\n`, [operations.libelle("inbox", "New requests")]));
    expect(await screen.findByText("2 changes not published yet")).toBeInTheDocument();
  });

  it("annuler défait une session de frappe d'un coup", async () => {
    await rendre();
    taper(`${LU}#`);
    taper(`${LU}# a`);
    taper(`${LU}# ab`);
    expect(await screen.findByText("1 change not published yet")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "undo" }));
    await waitFor(() => expect(screen.queryByText(/not published yet/)).toBeNull());
    expect((screen.getByLabelText("texte") as HTMLTextAreaElement).value).toBe(LU);
  });

  it("quitter avec un brouillon non publié demande confirmation", async () => {
    await rendre();
    const sansBrouillon = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(sansBrouillon);
    expect(sansBrouillon.defaultPrevented).toBe(false);
    taper(`${LU}# unsaved\n`);
    await screen.findByText("1 change not published yet");
    const avecBrouillon = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(avecBrouillon);
    expect(avecBrouillon.defaultPrevented).toBe(true);
  });

  it("l'onglet YAML n'a pas de bouton de publication à lui : il écrit dans le brouillon commun", async () => {
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <BrouillonProvider slug="rh" name="onboarding">
          <BarreDuBrouillon />
          <YamlPage />
        </BrouillonProvider>
      </QueryClientProvider>,
    );
    const champ = (await screen.findByLabelText("YAML of onboarding")) as HTMLTextAreaElement;
    expect(screen.queryByRole("button", { name: /publish/ })).toBeNull();
    fireEvent.change(champ, { target: { value: `${LU}# from the YAML tab\n` } });
    await waitFor(() => expect(screen.getByTestId("validation-yaml")).toHaveTextContent("✓ valid workflow"), { timeout: 2000 });
    expect(screen.getAllByRole("button", { name: /publish/ })).toHaveLength(1);
  });
});
