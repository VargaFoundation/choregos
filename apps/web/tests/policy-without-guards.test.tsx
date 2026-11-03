import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { Suspense } from "react";
import { describe, expect, it, vi } from "vitest";
import SettingsGroupPage from "@/app/p/[slug]/settings/[group]/page";

vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme" }) }));

// The policy keeps neither `approvals` nor `review.require_human_for_risk` (finding #286, ADR 0044):
// the settings screen must no longer list them among what the orchestrator applies.
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
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme", me: null }) }));
vi.mock("@/components/connectors", () => ({ Connectors: () => null }));
vi.mock("@/components/project-operations", () => ({ ProjectOperations: () => null }));
vi.mock("next/dynamic", () => ({
  default: () =>
    function Editor({ value, label }: { value: string; label: string }) {
      return <textarea aria-label={label} value={value} readOnly />;
    },
}));

describe("the policy screen does not promise a phantom guard", () => {
  it("says that approvals and review.require_human_for_risk do not apply, and where to declare the human", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => {
      render(
        <QueryClientProvider client={client}>
          <Suspense fallback={null}>
            <SettingsGroupPage params={Promise.resolve({ slug: "billing", group: "security" })} />
          </Suspense>
        </QueryClientProvider>,
      );
    });
    const note = await screen.findByTestId("politique-sans-gardes");
    expect(note.textContent).toContain("approvals and review.require_human_for_risk are accepted but not enforced");
    expect(note.textContent).toContain("declare a human transition in the workflow");
    // What the orchestrator applies no longer names the approvals.
    const applique = screen.getByText(/the same policy the orchestrator applies/);
    expect(applique.textContent).not.toMatch(/approvals/i);
  });
});
