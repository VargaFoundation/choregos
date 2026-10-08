import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CarteDuParcours } from "@/components/parcours/carte-du-parcours";
import { reduire, DEPART } from "@/components/parcours/lecteur";
import { api } from "@/lib/api";
import type { WorkItemJourney } from "@/lib/types";
import { runAccess, runDiff, runEvents } from "@/mocks/data";
import { parcoursEnAttente, parcoursEnCours, parcoursTermine } from "@/mocks/parcours";

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
    api: { ...reel.api, runEvents: vi.fn(), runAccess: vi.fn(), runDiff: vi.fn(), decide: vi.fn() },
  };
});
// Le flux SSE d'un run qui tourne : rien en direct ici, le journal stocké suffit.
vi.mock("@/lib/sse", () => ({ useEventStream: () => ({ events: [], connected: false, error: null }) }));

const MAINTENANT = Date.parse("2026-10-08T12:00:00Z");

function rendre(journey: WorkItemJourney = parcoursEnCours(MAINTENANT)) {
  vi.mocked(api.runEvents).mockResolvedValue(runEvents);
  vi.mocked(api.runAccess).mockResolvedValue(runAccess);
  vi.mocked(api.runDiff).mockResolvedValue(runDiff as never);
  vi.mocked(api.decide).mockResolvedValue({});
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <CarteDuParcours journey={journey} slug="billing-api" itemId={journey.work_item_id} />
    </QueryClientProvider>,
  );
}

const statut = (id: string) => screen.getByTestId(`etape-${id}`).getAttribute("data-statut");

afterEach(() => vi.useRealTimers());

describe("la carte animée du parcours (S22-02)", () => {
  it("en direct : chaque case dit qui agit, où il en est, et combien de tours il a pris", () => {
    rendre();
    expect(screen.getByTestId("parcours-maintenant")).toHaveTextContent("now in Reviewed by an agent");
    expect(screen.getByTestId("parcours-maintenant")).toHaveTextContent("Security reviewer working");
    expect(statut("t-security-review")).toBe("en_cours");
    expect(statut("t-review")).toBe("fait");
    expect(statut("t-release")).toBe("a_venir");
    expect(statut("t-address-review")).toBe("fait");
    const testeur = screen.getByTestId("etape-t-test");
    expect(testeur).toHaveAccessibleName(/Tester, agent, runs the checks — done — passed in round 3 — ✓ 420 tests pass — 3 attempts/);
    expect(screen.getByTestId("tour-t-review")).toHaveTextContent("approved in round 2");
    expect(screen.getByTestId("etape-t-approve-spec")).toHaveAccessibleName(/Product owners, person/);
    // Les arcs pris le disent : une fois, sans plus.
    const svg = screen.getByTestId("parcours").querySelector("svg")!;
    expect(svg.querySelector('[data-arc="implemented->planned"]')).toHaveAttribute("data-fois", "1");
    expect(svg.querySelector('[data-arc="pr_approved->fixing_ci"]')).toHaveAttribute("data-fois", "0");
    expect(svg.querySelector('[data-segment="reviewed"]')).toHaveAttribute("data-statut", "actif");
    expect(svg.querySelector('[data-etape="t-security-review"] .parcours-pouls')).not.toBeNull();
  });

  it("Replay rejoue la vie événement par événement, à la vitesse choisie, et le raconte", () => {
    vi.useFakeTimers();
    rendre();
    fireEvent.click(screen.getByRole("button", { name: "Replay the journey" }));
    expect(screen.getByTestId("parcours-moment")).toHaveTextContent("1 / 30");
    expect(statut("t-triage")).toBe("en_cours");
    expect(statut("t-security-review")).toBe("a_venir");
    expect(screen.getByTestId("parcours-recit")).toHaveTextContent("The work item arrives in To triage.");
    fireEvent.click(screen.getByRole("button", { name: "2×" }));
    act(() => {
      vi.advanceTimersByTime(550 * 4);
    });
    expect(screen.getByTestId("parcours-moment")).toHaveTextContent("5 / 30");
    expect(statut("t-triage")).toBe("fait");
    fireEvent.click(screen.getByRole("button", { name: "Pause the replay" }));
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(screen.getByTestId("parcours-moment")).toHaveTextContent("5 / 30");
    // Le direct revient où en est le ticket.
    fireEvent.click(screen.getByRole("button", { name: /^Live/ }));
    expect(statut("t-security-review")).toBe("en_cours");
  });

  it("une phase cliquée saute au moment où elle commence ; une phase jamais atteinte ne se clique pas", () => {
    rendre();
    const phases = screen.getByRole("navigation", { name: "stages of the journey" });
    fireEvent.click(within(phases).getByRole("button", { name: /In progress/ }));
    expect(statut("t-implement")).toBe("en_cours");
    expect(statut("t-test")).toBe("a_venir");
    expect(screen.getByTestId("parcours-recit")).toHaveTextContent("Planner moved it to Planned.");
    expect(within(phases).getByRole("button", { name: /Deployed/ })).toBeDisabled();
  });

  it("cliquer un agent ouvre ses tentatives, ce qu'il a fait et son journal", async () => {
    rendre();
    fireEvent.click(screen.getByTestId("etape-t-test"));
    const panneau = screen.getByTestId("panneau-parcours");
    expect(within(panneau).getAllByRole("tab", { name: /·/ })).toHaveLength(3);
    expect(within(panneau).getByText("420 tests pass.")).toBeInTheDocument();
    fireEvent.click(within(panneau).getByTestId("tentative-r-test-1"));
    expect(within(panneau).getByText("2 tests fail: rounding of partial credit notes.")).toBeInTheDocument();
    expect(within(panneau).getByRole("list", { name: "evidence" })).toHaveTextContent("✗ 412 run, 2 failed");
    const fait = await within(panneau).findByTestId("ce-qu-il-a-fait");
    await waitFor(() => expect(fait).toHaveTextContent("src/billing/rates.py"));
    expect(fait).toHaveTextContent("refused: outside the allowed paths");
    expect(fait).toHaveTextContent("tests/billing/test_totals.py");
    expect(fait).toHaveTextContent("evidence_present");
    expect(api.runAccess).toHaveBeenCalledWith("r-test-1");
    fireEvent.click(within(panneau).getByRole("tab", { name: "Log" }));
    await waitFor(() => expect(within(panneau).getByTestId("live-log")).toHaveTextContent("session/request_permission"));
    expect(within(panneau).getByRole("link", { name: "open the full run →" })).toHaveAttribute("href", "/p/billing-api/runs/r-test-1");
    fireEvent.click(within(panneau).getByRole("button", { name: "close the panel" }));
    expect(screen.queryByTestId("panneau-parcours")).not.toBeInTheDocument();
  });

  it("une personne attendue se décide depuis sa case", async () => {
    rendre(parcoursEnAttente(MAINTENANT));
    expect(statut("t-approve-spec")).toBe("attend");
    fireEvent.click(screen.getByTestId("etape-t-approve-spec"));
    const panneau = screen.getByTestId("panneau-parcours");
    expect(panneau).toHaveTextContent("decide here");
    fireEvent.click(within(panneau).getByRole("button", { name: "approve" }));
    await waitFor(() => expect(api.decide).toHaveBeenCalledWith("w2", { kind: "approve" }));
  });

  it("un ticket fini : tout est fait, la fin est atteinte, la CI rouge et sa réparation se voient", () => {
    rendre(parcoursTermine(MAINTENANT));
    expect(screen.getByTestId("parcours-maintenant")).toHaveTextContent("now in Verified in production");
    const svg = screen.getByTestId("parcours").querySelector("svg")!;
    expect([...svg.querySelectorAll("[data-etape]")].every((g) => g.getAttribute("data-statut") === "fait")).toBe(true);
    expect(svg.querySelector('[data-arc="pr_approved->fixing_ci"]')).toHaveAttribute("data-fois", "1");
    expect(screen.getByTestId("etape-t-release")).toHaveAccessibleName(/approved by leo/);
  });
});

describe("le lecteur (S22-02)", () => {
  it("joue jusqu'au bout puis s'arrête, et repart du début", () => {
    let etat = reduire(DEPART, { type: "lire", nombre: 3 });
    expect(etat).toMatchObject({ mode: "relecture", index: 0, enLecture: true });
    etat = reduire(reduire(etat, { type: "tic", nombre: 3 }), { type: "tic", nombre: 3 });
    expect(etat).toMatchObject({ index: 2, enLecture: true });
    etat = reduire(etat, { type: "tic", nombre: 3 });
    expect(etat).toMatchObject({ index: 2, enLecture: false });
    expect(reduire(etat, { type: "lire", nombre: 3 })).toMatchObject({ index: 0, enLecture: true });
    expect(reduire(etat, { type: "aller", index: 99, nombre: 3 })).toMatchObject({ index: 2, enLecture: false });
    expect(reduire(DEPART, { type: "lire", nombre: 0 })).toBe(DEPART);
  });
});
