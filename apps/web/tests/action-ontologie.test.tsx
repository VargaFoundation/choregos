import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { Suspense } from "react";
import { describe, expect, it, vi } from "vitest";
import ActionPage from "@/app/p/[slug]/actions/[id]/page";
import { DecisionDAction } from "@/components/actions/decision";
import { DetailOntologie } from "@/components/actions/ontologie";
import { api } from "@/lib/api";
import type { Action } from "@/lib/types";
import config from "../next.config.mjs";

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return { ...reel, api: { ...reel.api, projectAction: vi.fn() } };
});

const ontologie = {
  id: "pr1",
  origin: "ontology",
  kind: "ontology.open_infra_pr",
  title: "open_infra_pr on os-reboot-required",
  status: "succeeded",
  params: {
    ontologie: {
      action_type: "open_infra_pr",
      target_ids: ["os-reboot-required"],
      params: { path: "platform/maintenance/reboot.yaml" },
    },
  },
  result: {
    effects: [{ index: 0, type: "gitops.pull_request", status: "done", url: "https://github.com/acme/infra/pull/7" }],
    evidence: [{ index: 0, name: "finding_gone", status: "passed", expect: "!result.key_present" }],
  },
} as unknown as Action;

describe("une action de l'ontologie dit ce qu'elle touche (S20-10)", () => {
  it("son type, ses cibles, ses paramètres, ce que ses effets ont rendu et ses preuves recueilli", () => {
    render(<DetailOntologie action={ontologie} />);
    const carte = screen.getByTestId("ontologie");
    expect(carte).toHaveTextContent("open_infra_pr");
    expect(carte).toHaveTextContent("os-reboot-required");
    expect(carte).toHaveTextContent("platform/maintenance/reboot.yaml");
    const dossier = screen.getByTestId("ontologie-dossier");
    expect(dossier).toHaveTextContent("gitops.pull_request");
    expect(dossier).toHaveTextContent("https://github.com/acme/infra/pull/7");
    expect(dossier).toHaveTextContent("finding_gone");
    expect(dossier).toHaveTextContent("passed");
  });

  it("la page d'une action la montre", async () => {
    vi.mocked(api.projectAction).mockResolvedValue({ ...ontologie, effects: [], decisions: [], journal: [] } as Action);
    // La page lit ses paramètres par `use(promesse)` : elle suspend, et le rendu s'attend.
    await act(async () => {
      render(
        <QueryClientProvider client={new QueryClient()}>
          <Suspense fallback="…">
            <ActionPage params={Promise.resolve({ slug: "infra", id: "pr1" })} />
          </Suspense>
        </QueryClientProvider>,
      );
    });
    expect(await screen.findByTestId("ontologie")).toHaveTextContent("os-reboot-required");
  });

  it("une action d'une autre origine n'a pas cette carte", () => {
    const { container } = render(<DetailOntologie action={{ ...ontologie, origin: "tool" } as Action} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("une règle sans authentification récente ne la promet pas", () => {
    const enAttente = { ...ontologie, status: "pending_approval" } as Action;
    const { unmount } = render(
      <QueryClientProvider client={new QueryClient()}>
        <DecisionDAction projet="infra" action={{ ...enAttente, approval: { step_up_minutes: 10 } } as Action} />
      </QueryClientProvider>,
    );
    expect(screen.getByTestId("regles-de-decision")).toHaveTextContent("within 10 min");
    unmount();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <DecisionDAction projet="infra" action={{ ...enAttente, approval: {} } as Action} />
      </QueryClientProvider>,
    );
    expect(screen.getByTestId("regles-de-decision")).not.toHaveTextContent("recent sign-in");
  });

  it("les anciens liens des propositions mènent aux actions, sous le même identifiant", async () => {
    const redirections = await config.redirects?.();
    expect(redirections).toEqual(
      expect.arrayContaining([
        { source: "/p/:slug/proposals", destination: "/p/:slug/actions", permanent: true },
        { source: "/p/:slug/proposals/:id", destination: "/p/:slug/actions/:id", permanent: true },
      ]),
    );
  });
});
