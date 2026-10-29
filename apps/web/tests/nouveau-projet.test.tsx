import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import NewProjectPage from "@/app/projects/new/page";
import { api } from "@/lib/api";

const pousser = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: pousser }) }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme" }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
    api: {
      ...reel.api,
      templates: vi.fn().mockResolvedValue([]),
      createProject: vi.fn().mockResolvedValue({ id: "p1", slug: "people-ops" }),
      putConnector: vi
        .fn()
        .mockRejectedValueOnce(new Error("tracker unreachable"))
        .mockResolvedValue({}),
      provision: vi.fn(),
      workflowCatalogue: vi.fn().mockResolvedValue([
        { source: "core", name: "default-request", version: 1, title: "default-request", needs_repository: false, agents: [] },
        { source: "core", name: "default-simple", version: 1, title: "default-simple", needs_repository: true, agents: [] },
        { source: "template:joiners-leavers", name: "onboarding", version: 1, title: "Onboarding", needs_repository: false, agents: [] },
      ]),
      adoptWorkflow: vi.fn().mockResolvedValue({}),
    },
  };
});

function ouvrir() {
  document.body.innerHTML = "";
  render(
    <QueryClientProvider client={new QueryClient()}>
      <NewProjectPage />
    </QueryClientProvider>,
  );
}

/** Jusqu'au récapitulatif, sans dépôt. */
function remplir() {
  fireEvent.click(screen.getByLabelText(/no code repository/));
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  fireEvent.change(screen.getByLabelText("Identifier (slug)"), { target: { value: "people-ops" } });
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
}

describe("l'assistant de création d'un projet (S23-06)", () => {
  it("dit pourquoi « next » attend, et récapitule en mots, pas en clés", () => {
    ouvrir();
    fireEvent.click(screen.getByLabelText(/no code repository/));
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Next" })).toHaveAccessibleDescription("an identifier is needed to go on");
    fireEvent.change(screen.getByLabelText("Identifier (slug)"), { target: { value: "people-ops" } });
    expect(screen.queryByText("An identifier is needed to go on")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    fireEvent.click(screen.getByRole("button", { name: "Next" }));

    const recap = screen.getByTestId("recapitulatif");
    expect(recap).toHaveTextContent("Identifier");
    expect(recap).not.toHaveTextContent("sansDepot");
  });

  it("reprend là où il a échoué, sans recréer le projet", async () => {
    ouvrir();
    remplir();

    // Le projet se crée, le tracker est refusé : l'erreur le dit, et « retry » ne recrée pas le projet.
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByText(/people-ops was created, but connecting its tracker failed/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "open the project" })).toHaveAttribute("href", "/p/people-ops");
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(pousser).toHaveBeenCalledWith("/p/people-ops"));
    expect(api.createProject).toHaveBeenCalledTimes(1);
    expect(api.putConnector).toHaveBeenCalledTimes(2);
  });

  it("without a repository, offers only workflows that need none, and adopts the chosen one as the default", async () => {
    ouvrir();
    fireEvent.click(screen.getByLabelText(/no code repository/));
    const choice = screen.getByLabelText("Workflow");
    await screen.findByRole("option", { name: /Onboarding/ });
    expect(screen.queryByRole("option", { name: /^default-simple/ })).toBeNull();
    fireEvent.change(choice, { target: { value: "template:joiners-leavers/onboarding" } });
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    fireEvent.change(screen.getByLabelText("Identifier (slug)"), { target: { value: "people-ops" } });
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByTestId("recapitulatif")).toHaveTextContent("Onboarding (template:joiners-leavers)");
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() =>
      expect(api.adoptWorkflow).toHaveBeenCalledWith("p1", {
        source: "template:joiners-leavers",
        name: "onboarding",
        version: 1,
        make_default: true,
      }),
    );
  });
});
