import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { Suspense } from "react";
import { describe, expect, it, vi } from "vitest";
import ActionPage from "@/app/p/[slug]/actions/[id]/page";
import { DecisionDAction } from "@/components/actions/decision";
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

describe("an action the ontology persisted is still read (ADR 0181, O4-1)", () => {
  it("its page shows it like any other action, with no card of the removed extension", async () => {
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
    expect(await screen.findByText("open_infra_pr on os-reboot-required")).toBeInTheDocument();
    expect(screen.getByText("ontology.open_infra_pr")).toBeInTheDocument();
    expect(screen.queryByTestId("ontology")).not.toBeInTheDocument();
  });

  it("a rule without a recent authentication does not promise it", () => {
    const pending = { ...ontology, status: "pending_approval" } as Action;
    const { unmount } = render(
      <QueryClientProvider client={new QueryClient()}>
        <DecisionDAction projet="infra" action={{ ...pending, approval: { step_up_minutes: 10 } } as Action} />
      </QueryClientProvider>,
    );
    expect(screen.getByTestId("decision-rules")).toHaveTextContent("within 10 min");
    unmount();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <DecisionDAction projet="infra" action={{ ...pending, approval: {} } as Action} />
      </QueryClientProvider>,
    );
    expect(screen.getByTestId("decision-rules")).not.toHaveTextContent("recent sign-in");
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
