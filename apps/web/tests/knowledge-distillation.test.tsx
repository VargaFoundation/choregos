import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Suspense } from "react";
import { describe, expect, it, vi } from "vitest";
import KnowledgeBasePage from "@/app/knowledge/[slug]/page";
import { api } from "@/lib/api";
import { distillationLabel, distillationProgress } from "@/lib/knowledge";
import type { KnowledgeBase, KnowledgeDistillEstimate } from "@/lib/types";

vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "varga" }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      knowledgeBase: vi.fn(),
      distillKnowledgeBase: vi.fn(),
      estimateKnowledgeDistillation: vi.fn(),
      updateKnowledgeBase: vi.fn(),
    },
  };
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

  it("says where a running distillation is, and why a failed one stopped", () => {
    expect(distillationProgress({ phase: "map", parts_done: 3, parts_total: 8, spent_usd: 0.92 })).toBe("map 3/8 \u00b7 $0.92 spent");
    expect(distillationLabel({ status: "running", phase: "map", parts_done: 3, parts_total: 8, spent_usd: 0.92 })).toBe(
      "distilling\u2026 map 3/8 \u00b7 $0.92 spent",
    );
    expect(distillationLabel({ status: "failed", code: "over_budget", error: "stopped before part 3", spent_usd: 8 })).toBe(
      "failed: stopped before the budget was spent (stopped before part 3) \u00b7 $8.00 spent",
    );
    expect(distillationLabel({ status: "failed", code: "too_large", error: "this base holds 2.26 M characters" })).toContain(
      "leave the biggest documents out",
    );
  });

  async function open() {
    vi.mocked(api.knowledgeBase).mockResolvedValue(base);
    vi.mocked(api.distillKnowledgeBase).mockResolvedValue(base);
    await act(async () => {
      render(
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <Suspense fallback="\u2026">
            <KnowledgeBasePage params={Promise.resolve({ slug: "architecture" })} />
          </Suspense>
        </QueryClientProvider>,
      );
    });
    await screen.findByTestId("knowledge-distillation");
  }

  it("links the skill to review", async () => {
    await open();
    expect(screen.getByTestId("knowledge-distillation")).toHaveTextContent("waiting for review");
    expect(screen.getByRole("link", { name: "Review" })).toHaveAttribute("href", "/skills/architecture-principles");
  });

  const estimate: KnowledgeDistillEstimate = {
    characters: 2261896,
    tokens_estimate: 565474,
    documents: 3,
    parts: 8,
    calls: 9,
    model: "platform/standard",
    input_cost_usd: 1.5,
    output_cost_usd: 0.5,
    priced_by: "override",
    budget_usd: 10,
    within_budget: true,
    refused_reason: null,
    biggest: [
      { filename: "budget.xlsx", characters: 1500000 },
      { filename: "plan.md", characters: 60000 },
    ],
  };

  it("shows what the run would read and cost before starting it, then sends the exclusions and a cap", async () => {
    vi.mocked(api.estimateKnowledgeDistillation).mockResolvedValue(estimate);
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Distill" }));
    expect(await screen.findByTestId("estimate-reads")).toHaveTextContent("2,261,896 characters (\u2248 565,474 tokens)");
    expect(screen.getByTestId("estimate-calls")).toHaveTextContent("8 parts, 9 model calls");
    expect(screen.getByTestId("estimate-cost")).toHaveTextContent("$2.00");
    expect(api.estimateKnowledgeDistillation).toHaveBeenCalledWith("varga", "architecture", []);
    expect(api.distillKnowledgeBase).not.toHaveBeenCalled();

    vi.mocked(api.estimateKnowledgeDistillation).mockResolvedValue({ ...estimate, parts: 1, calls: 1, documents: 2 });
    fireEvent.click(screen.getByRole("checkbox", { name: /budget\.xlsx/ }));
    await waitFor(() => expect(api.estimateKnowledgeDistillation).toHaveBeenLastCalledWith("varga", "architecture", ["budget.xlsx"]));
    await waitFor(() => expect(screen.getByTestId("estimate-calls")).toHaveTextContent("1 part, 1 model call"));

    fireEvent.click(screen.getByRole("button", { name: "Start distillation" }));
    await waitFor(() =>
      expect(api.distillKnowledgeBase).toHaveBeenCalledWith("varga", "architecture", { exclude: ["budget.xlsx"], max_usd: 2.5 }),
    );
  });

  it("says when the model is not priced and sends no cap", async () => {
    vi.mocked(api.estimateKnowledgeDistillation).mockResolvedValue({
      ...estimate,
      input_cost_usd: null,
      output_cost_usd: null,
      priced_by: "none",
    });
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Distill" }));
    expect(await screen.findByTestId("estimate-cost")).toHaveTextContent("not priced");
    fireEvent.click(screen.getByRole("button", { name: "Start distillation" }));
    await waitFor(() => expect(api.distillKnowledgeBase).toHaveBeenCalledWith("varga", "architecture", {}));
  });

  it("refuses to start what the platform would refuse, and says why", async () => {
    vi.mocked(api.estimateKnowledgeDistillation).mockResolvedValue({
      ...estimate,
      refused_reason: "this base holds 2.26 M characters: exclude `*.xlsx`",
    });
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Distill" }));
    expect(await screen.findByTestId("estimate-refused")).toHaveTextContent("exclude `*.xlsx`");
    expect(screen.getByRole("button", { name: "Start distillation" })).toBeDisabled();
  });

  it("switches the serving mode files to index through the existing PATCH (S42-09b)", async () => {
    vi.mocked(api.knowledgeBase).mockResolvedValue(base);
    vi.mocked(api.updateKnowledgeBase).mockResolvedValue({ ...base, serving_mode: "index" });
    await act(async () => {
      render(
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <Suspense fallback="…">
            <KnowledgeBasePage params={Promise.resolve({ slug: "architecture" })} />
          </Suspense>
        </QueryClientProvider>,
      );
    });
    const row = await screen.findByTestId("knowledge-serving");
    expect(row).toHaveTextContent("recommended above ~200k tokens");
    fireEvent.change(screen.getByLabelText("serving mode"), { target: { value: "index" } });
    await waitFor(() => expect(api.updateKnowledgeBase).toHaveBeenCalledWith("varga", "architecture", { serving_mode: "index" }));
  });
});
