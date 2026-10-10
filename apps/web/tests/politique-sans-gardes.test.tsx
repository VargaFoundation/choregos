import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { Suspense } from "react";
import { describe, expect, it, vi } from "vitest";
import SettingsPage from "@/app/p/[slug]/settings/page";

// La politique ne garde ni `approvals` ni `review.require_human_for_risk` (finding #286, ADR 0044) :
// l'écran des paramètres ne doit plus les ranger parmi ce que l'orchestrateur applique.
vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
    api: {
      ...reel.api,
      policy: vi.fn(async () => ({ id: "p", name: "solo", version: 1, yaml: "kind: Policy\n", is_active: true })),
      modelMatrix: vi.fn(async () => ({ entries: [] })),
      projectTools: vi.fn(async () => ({ tools: [], allows_all: false })),
      models: vi.fn(async () => ({ profiles: {}, allow_unvalidated: false, inherited: {} })),
      platformModels: vi.fn(async () => []),
    },
  };
});
vi.mock("@/components/connecteurs", () => ({ Connecteurs: () => null }));
vi.mock("@/components/operations-du-projet", () => ({ OperationsDuProjet: () => null }));
vi.mock("next/dynamic", () => ({
  default: () =>
    function Editeur({ value, label }: { value: string; label: string }) {
      return <textarea aria-label={label} value={value} readOnly />;
    },
}));

describe("l'écran de la politique ne promet pas de garde fantôme", () => {
  it("dit que approvals et review.require_human_for_risk ne s'appliquent pas, et où déclarer l'humain", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => {
      render(
        <QueryClientProvider client={client}>
          <Suspense fallback={null}>
            <SettingsPage params={Promise.resolve({ slug: "billing" })} />
          </Suspense>
        </QueryClientProvider>,
      );
    });
    const note = await screen.findByTestId("politique-sans-gardes");
    expect(note.textContent).toContain("approvals and review.require_human_for_risk are accepted but not enforced");
    expect(note.textContent).toContain("declare a human transition in the workflow");
    // Ce que l'orchestrateur applique ne nomme plus les approbations.
    const applique = screen.getByText(/the same policy the orchestrator applies/);
    expect(applique.textContent).not.toMatch(/approvals/i);
  });
});
