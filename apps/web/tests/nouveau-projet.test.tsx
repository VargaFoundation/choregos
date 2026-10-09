import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import NewProjectPage from "@/app/projects/new/page";
import { api } from "@/lib/api";

const pousser = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: pousser }) }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "varga" }) }));
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
  fireEvent.click(screen.getByRole("button", { name: "next" }));
  fireEvent.change(screen.getByLabelText("identifier (slug)"), { target: { value: "people-ops" } });
  fireEvent.click(screen.getByRole("button", { name: "next" }));
  fireEvent.click(screen.getByRole("button", { name: "next" }));
}

describe("l'assistant de création d'un projet (S23-06)", () => {
  it("dit pourquoi « next » attend, et récapitule en mots, pas en clés", () => {
    ouvrir();
    fireEvent.click(screen.getByLabelText(/no code repository/));
    fireEvent.click(screen.getByRole("button", { name: "next" }));
    expect(screen.getByRole("button", { name: "next" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "next" })).toHaveAccessibleDescription("an identifier is needed to go on");
    fireEvent.change(screen.getByLabelText("identifier (slug)"), { target: { value: "people-ops" } });
    expect(screen.queryByText("an identifier is needed to go on")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "next" }));
    fireEvent.click(screen.getByRole("button", { name: "next" }));

    const recap = screen.getByTestId("recapitulatif");
    expect(recap).toHaveTextContent("identifier");
    expect(recap).not.toHaveTextContent("sansDepot");
  });

  it("reprend là où il a échoué, sans recréer le projet", async () => {
    ouvrir();
    remplir();

    // Le projet se crée, le tracker est refusé : l'erreur le dit, et « retry » ne recrée pas le projet.
    fireEvent.click(screen.getByRole("button", { name: "create" }));
    expect(await screen.findByText(/people-ops was created, but connecting its tracker failed/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "open the project" })).toHaveAttribute("href", "/p/people-ops");
    expect(screen.getByRole("button", { name: "previous" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "retry" }));
    await waitFor(() => expect(pousser).toHaveBeenCalledWith("/p/people-ops"));
    expect(api.createProject).toHaveBeenCalledTimes(1);
    expect(api.putConnector).toHaveBeenCalledTimes(2);
  });
});
