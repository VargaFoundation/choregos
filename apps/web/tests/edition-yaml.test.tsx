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
// The real editor (CodeMirror) has its own tests: here, a text field is enough to type.
vi.mock("next/dynamic", () => ({
  default: () =>
    function Editor({ value, onChange, label }: { value: string; onChange: (v: string) => void; label: string }) {
      return <textarea aria-label={label} value={value} onChange={(e) => onChange(e.target.value)} />;
    },
}));
vi.mock("next/navigation", () => ({ usePathname: () => "/p/rh/workflows/onboarding/yaml" }));

const LU = "apiVersion: choregos/v1\nkind: Workflow\nmetadata: { name: onboarding, version: 3 }\nstates:\n  inbox: { display: Inbox }\n";
const graph = { nodes: [{ id: "inbox", display: "Inbox", kind: "wait", lane: "agent" }], edges: [] };
const valid: WorkflowValidation = { valid: true, errors: [], warnings: [], graph: graph, process: [] };
const invalide: WorkflowValidation = {
  valid: false,
  errors: [{ code: "workflow.states", message: "a workflow declares its states", line: 1, column: 1 }],
  warnings: [],
};
const version = { courante: 3 };

function Sonde() {
  const draft = useBrouillon();
  return (
    <div>
      <textarea aria-label="texte" value={draft.yaml} onChange={(e) => draft.ecrire(e.target.value)} />
      <p data-testid="validation">{draft.validation.etat}</p>
      <button type="button" onClick={() => void draft.appliquer(operations.libelle("inbox", "New requests"))}>
        geste
      </button>
    </div>
  );
}

/** A tab that is left and returned to: the draft's provider stays (it lives in the layout). */
function Tabs({ children }: { children: ReactNode }) {
  const [visible, setVisible] = useState(true);
  return (
    <>
      <button type="button" onClick={() => setVisible(!visible)}>
        switch tab
      </button>
      {visible && children}
    </>
  );
}

let client: QueryClient;
async function mount(contenu: ReactNode = <Sonde />) {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <BrouillonProvider slug="rh" name="onboarding">
        <BarreDuBrouillon />
        <Tabs>{contenu}</Tabs>
      </BrouillonProvider>
    </QueryClientProvider>,
  );
  await waitFor(() => expect((screen.getAllByRole("textbox")[0] as HTMLTextAreaElement).value).toContain("apiVersion"));
}

const taper = (texte: string) => fireEvent.change(screen.getByLabelText("texte"), { target: { value: texte } });
const validAtTheEnd = () => waitFor(() => expect(screen.getByTestId("validation")).toHaveTextContent("a_jour"), { timeout: 2000 });

beforeEach(() => {
  version.courante = 3;
  vi.mocked(api.workflowNamed).mockImplementation(
    async () => ({ name: "onboarding", version: version.courante, yaml: LU, is_active: true, json: { actors: {} } }) as never,
  );
  vi.mocked(api.validateWorkflow).mockReset().mockResolvedValue(valid);
  vi.mocked(api.editWorkflow).mockReset();
  vi.mocked(api.putWorkflowNamed).mockReset().mockResolvedValue({ name: "onboarding", version: 4 } as never);
});

describe("the single draft of the three views (S21-06)", () => {
  it("the typed text becomes the draft, and is published with the version read at the start", async () => {
    await mount();
    taper(`${LU}# typed\n`);
    expect(await screen.findByText("1 change not published yet")).toBeInTheDocument();
    await validAtTheEnd();
    fireEvent.click(screen.getByRole("button", { name: "Publish v4" }));
    await waitFor(() => expect(api.putWorkflowNamed).toHaveBeenCalledWith("rh", "onboarding", `${LU}# typed\n`, 3));
  });

  it("the text survives a tab change", async () => {
    await mount();
    taper(`${LU}# kept\n`);
    fireEvent.click(screen.getByRole("button", { name: "switch tab" }));
    expect(screen.queryByLabelText("texte")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "switch tab" }));
    expect((screen.getByLabelText("texte") as HTMLTextAreaElement).value).toBe(`${LU}# kept\n`);
  });

  it("a stale validation does not replace the latest one", async () => {
    let lente: (v: WorkflowValidation) => void = () => {};
    vi.mocked(api.validateWorkflow).mockImplementation((texte: string) =>
      texte === "kind: Workflow\n" ? new Promise((resolve) => (lente = resolve)) : Promise.resolve(valid),
    );
    await mount();
    taper("kind: Workflow\n");
    await waitFor(() => expect(api.validateWorkflow).toHaveBeenCalledWith("kind: Workflow\n", expect.anything()), {
      timeout: 2000,
    });
    taper(`${LU}# fixed\n`);
    await validAtTheEnd();
    // The first answer arrives AFTER the second: it must write nothing.
    act(() => lente(invalide));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText(/Would be invalid/)).toBeNull();
    expect(screen.getByRole("button", { name: "Publish v4" })).toBeEnabled();
  });

  it("a stale answer that arrives during the next validation does not unblock publishing", async () => {
    const pending = new Map<string, (v: WorkflowValidation) => void>();
    vi.mocked(api.validateWorkflow).mockImplementation((texte: string) =>
      texte === LU ? Promise.resolve(valid) : new Promise((resolve) => pending.set(texte, resolve)),
    );
    await mount();
    taper(`${LU}# A\n`);
    await waitFor(() => expect(pending.has(`${LU}# A\n`)).toBe(true), { timeout: 2000 });
    taper(`${LU}# B\n`);
    await waitFor(() => expect(pending.has(`${LU}# B\n`)).toBe(true), { timeout: 2000 });
    // A answers while B is still in flight: A's verdict does not hold for B.
    await act(async () => pending.get(`${LU}# A\n`)!(valid));
    expect(screen.getByTestId("validation")).toHaveTextContent("en_cours");
    expect(screen.getByRole("button", { name: "Publish v4" })).toBeDisabled();
    await act(async () => pending.get(`${LU}# B\n`)!(valid));
    await validAtTheEnd();
    expect(screen.getByRole("button", { name: "Publish v4" })).toBeEnabled();
  });

  it("an unreachable validation says so and forbids publishing; retrying relaunches it", async () => {
    let calls = 0;
    vi.mocked(api.validateWorkflow).mockImplementation(async (texte: string) => {
      if (texte === LU) return valid; // the published version
      calls += 1;
      if (calls === 1) throw new Error("network down");
      return valid;
    });
    await mount();
    taper(`${LU}# offline\n`);
    expect(await screen.findByText(/Could not validate the text: network down/, {}, { timeout: 2000 })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Publish v4" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await validAtTheEnd();
    expect(screen.getByRole("button", { name: "Publish v4" })).toBeEnabled();
  });

  it("an invalid text forbids publishing", async () => {
    vi.mocked(api.validateWorkflow).mockResolvedValue(invalide);
    await mount();
    taper("kind: Workflow\n");
    await validAtTheEnd();
    expect(screen.getByText(/the workflow would be invalid/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Publish v4" })).toBeDisabled();
  });

  it("a version published during the edit does not replace the text and opens the conflict", async () => {
    await mount();
    taper(`${LU}# mine\n`);
    await validAtTheEnd();
    version.courante = 4;
    await act(() => client.invalidateQueries({ queryKey: ["workflow", "rh", "onboarding"] }));
    expect(await screen.findByTestId("conflit")).toHaveTextContent("you started from v3, the workflow is now v4");
    expect((screen.getByLabelText("texte") as HTMLTextAreaElement).value).toBe(`${LU}# mine\n`);
    expect(screen.getByRole("button", { name: "Publish v5" })).toBeDisabled();
  });

  it("a 409 keeps the text and offers to reload or overwrite", async () => {
    vi.mocked(api.putWorkflowNamed)
      .mockRejectedValueOnce(new ApiError(409, { title: "Conflict" }))
      .mockResolvedValueOnce({ name: "onboarding", version: 5 } as never);
    await mount();
    taper(`${LU}# mine\n`);
    await validAtTheEnd();
    version.courante = 4;
    fireEvent.click(screen.getByRole("button", { name: "Publish v4" }));
    expect(await screen.findByTestId("conflit")).toBeInTheDocument();
    expect((screen.getByLabelText("texte") as HTMLTextAreaElement).value).toBe(`${LU}# mine\n`);
    fireEvent.click(screen.getByRole("button", { name: "Publish over v4" }));
    await waitFor(() => expect(api.putWorkflowNamed).toHaveBeenLastCalledWith("rh", "onboarding", `${LU}# mine\n`, 4));
  });

  it("a gesture on the map applies to the edited text", async () => {
    const outcome = { ...valid, yaml: `${LU}# typed\n# grafted\n`, diff: "+# grafted", inverse: [], notices: [] } as WorkflowEditResult;
    vi.mocked(api.editWorkflow).mockResolvedValue(outcome);
    await mount();
    taper(`${LU}# typed\n`);
    fireEvent.click(screen.getByRole("button", { name: "geste" }));
    await waitFor(() => expect(api.editWorkflow).toHaveBeenCalledWith(`${LU}# typed\n`, [operations.libelle("inbox", "New requests")]));
    expect(await screen.findByText("2 changes not published yet")).toBeInTheDocument();
  });

  it("undo reverts a typing session at once", async () => {
    await mount();
    taper(`${LU}#`);
    taper(`${LU}# a`);
    taper(`${LU}# ab`);
    expect(await screen.findByText("1 change not published yet")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(screen.queryByText(/Not published yet/)).toBeNull());
    expect((screen.getByLabelText("texte") as HTMLTextAreaElement).value).toBe(LU);
  });

  it("leaving with an unpublished draft asks for confirmation", async () => {
    await mount();
    const withoutDraft = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(withoutDraft);
    expect(withoutDraft.defaultPrevented).toBe(false);
    taper(`${LU}# unsaved\n`);
    await screen.findByText("1 change not published yet");
    const withDraft = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(withDraft);
    expect(withDraft.defaultPrevented).toBe(true);
  });

  it("the YAML tab has no publish button of its own: it writes into the shared draft", async () => {
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <BrouillonProvider slug="rh" name="onboarding">
          <BarreDuBrouillon />
          <YamlPage />
        </BrouillonProvider>
      </QueryClientProvider>,
    );
    const field = (await screen.findByLabelText("YAML of onboarding")) as HTMLTextAreaElement;
    expect(screen.queryByRole("button", { name: /Publish/ })).toBeNull();
    fireEvent.change(field, { target: { value: `${LU}# from the YAML tab\n` } });
    await waitFor(() => expect(screen.getByTestId("validation-yaml")).toHaveTextContent("✓ valid workflow"), { timeout: 2000 });
    expect(screen.getAllByRole("button", { name: /Publish/ })).toHaveLength(1);
  });
});
