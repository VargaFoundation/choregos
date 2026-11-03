import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { Suspense } from "react";
import { describe, expect, it, vi } from "vitest";
import ActionPage from "@/app/p/[slug]/actions/[id]/page";
import { DecisionDAction } from "@/components/actions/decision";
import { OntologyDetail } from "@/components/actions/ontology";
import { api } from "@/lib/api";
import type { Action } from "@/lib/types";
import config from "../next.config.mjs";

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return { ...reel, api: { ...reel.api, projectAction: vi.fn() } };
});

const ontology = {
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

describe("an ontology action says what it touches (S20-10)", () => {
  it("its type, its targets, its parameters, what its effects returned and its evidence gathered", () => {
    render(<OntologyDetail action={ontology} />);
    const card = screen.getByTestId("ontologie");
    expect(card).toHaveTextContent("open_infra_pr");
    expect(card).toHaveTextContent("os-reboot-required");
    expect(card).toHaveTextContent("platform/maintenance/reboot.yaml");
    const dossier = screen.getByTestId("ontologie-dossier");
    expect(dossier).toHaveTextContent("gitops.pull_request");
    expect(dossier).toHaveTextContent("https://github.com/acme/infra/pull/7");
    expect(dossier).toHaveTextContent("finding_gone");
    expect(dossier).toHaveTextContent("passed");
  });

  it("an action's page shows it", async () => {
    vi.mocked(api.projectAction).mockResolvedValue({ ...ontology, effects: [], decisions: [], journal: [] } as Action);
    // The page reads its parameters through `use(promise)`: it suspends, and the render has to be awaited.
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

  it("an action of another origin does not have this card", () => {
    const { container } = render(<OntologyDetail action={{ ...ontology, origin: "tool" } as Action} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("a rule without a recent authentication does not promise it", () => {
    const pending = { ...ontology, status: "pending_approval" } as Action;
    const { unmount } = render(
      <QueryClientProvider client={new QueryClient()}>
        <DecisionDAction projet="infra" action={{ ...pending, approval: { step_up_minutes: 10 } } as Action} />
      </QueryClientProvider>,
    );
    expect(screen.getByTestId("regles-de-decision")).toHaveTextContent("within 10 min");
    unmount();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <DecisionDAction projet="infra" action={{ ...pending, approval: {} } as Action} />
      </QueryClientProvider>,
    );
    expect(screen.getByTestId("regles-de-decision")).not.toHaveTextContent("recent sign-in");
  });

  it("the old links of the proposals lead to the actions, under the same identifier", async () => {
    const redirections = await config.redirects?.();
    expect(redirections).toEqual(
      expect.arrayContaining([
        { source: "/p/:slug/proposals", destination: "/p/:slug/actions", permanent: true },
        { source: "/p/:slug/proposals/:id", destination: "/p/:slug/actions/:id", permanent: true },
      ]),
    );
  });
});
