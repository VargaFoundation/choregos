import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryAbReportCard } from "@/components/memory-ab-report";
import { api } from "@/lib/api";
import type { MemoryAbReport } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, memoryAbReport: vi.fn() } };
});

const report = (weeks: number, verdict: string): MemoryAbReport => ({
  org: "acme",
  weeks,
  verdict,
  detail: "Small groups.",
  with_memory: { projects: ["billing-api"], tickets: 18, first_pass_merge_rate: 0.72, cost_per_ticket_usd: 1.8 },
  without_memory: { projects: ["checkout-web"], tickets: 15, first_pass_merge_rate: 0.53, cost_per_ticket_usd: 2.4 },
});

describe("the memory A/B report", () => {
  beforeEach(() => {
    vi.mocked(api.memoryAbReport).mockReset();
    vi.mocked(api.memoryAbReport).mockImplementation(async (_org, weeks = 4) =>
      report(weeks, weeks === 12 ? "Memory helps over 12 weeks." : "Too early to tell."),
    );
  });

  it("loads the report for the chosen weeks", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryAbReportCard org="acme" />
      </QueryClientProvider>,
    );
    expect(await screen.findByText("Too early to tell.")).toBeInTheDocument();
    expect(api.memoryAbReport).toHaveBeenCalledWith("acme", 4);
    fireEvent.change(screen.getByLabelText("Period"), { target: { value: "12" } });
    await waitFor(() => expect(api.memoryAbReport).toHaveBeenCalledWith("acme", 12));
    expect(await screen.findByText("Memory helps over 12 weeks.")).toBeInTheDocument();
    expect(screen.getByText("72 %")).toBeInTheDocument();
    expect(screen.getByText("billing-api")).toBeInTheDocument();
  });
});
