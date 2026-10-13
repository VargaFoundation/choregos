import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Suspense } from "react";
import { describe, expect, it, vi } from "vitest";
import KnowledgeBasePage from "@/app/knowledge/[slug]/page";
import { api } from "@/lib/api";
import { distillationLabel } from "@/lib/knowledge";
import type { KnowledgeBase } from "@/lib/types";

vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "varga" }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, knowledgeBase: vi.fn(), distillKnowledgeBase: vi.fn() } };
});

const base: KnowledgeBase = {
  slug: "architecture",
  name: "Architecture framework",
  scope: "org",
  serving_mode: "files",
  status: "active",
  documents: [],
  distillation: { status: "pending_review", skill: "architecture-principles", version: 2, principles: 12, dropped: ["“Quantum”: cites no document of the base"] },
};

describe("distillation (S33-05)", () => {
  it("says what the last distillation gave", () => {
    expect(distillationLabel(null)).toBe("not distilled");
    expect(distillationLabel({ status: "running" })).toBe("distilling…");
    expect(distillationLabel({ status: "failed", error: "no converted document" })).toBe("failed: no converted document");
    expect(distillationLabel(base.distillation)).toBe("architecture-principles v2: 12 principles, waiting for review (1 dropped: no source)");
  });

  it("links the skill to review and distils again", async () => {
    vi.mocked(api.knowledgeBase).mockResolvedValue(base);
    vi.mocked(api.distillKnowledgeBase).mockResolvedValue(base);
    await act(async () => {
      render(
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <Suspense fallback="…">
            <KnowledgeBasePage params={Promise.resolve({ slug: "architecture" })} />
          </Suspense>
        </QueryClientProvider>,
      );
    });
    const line = await screen.findByTestId("knowledge-distillation");
    expect(line).toHaveTextContent("waiting for review");
    expect(screen.getByRole("link", { name: "review" })).toHaveAttribute("href", "/skills/architecture-principles");
    fireEvent.click(screen.getByRole("button", { name: "distill" }));
    await waitFor(() => expect(api.distillKnowledgeBase).toHaveBeenCalledWith("varga", "architecture"));
  });
});
