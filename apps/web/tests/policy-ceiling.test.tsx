import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PolicyCeilingCard, parseCeiling } from "@/components/admin/policy-ceiling";
import { api } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, policyCeiling: vi.fn(), putPolicyCeiling: vi.fn() } };
});

function wrap(node: React.ReactNode) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{node}</QueryClientProvider>);
}

describe("the organisation's policy ceiling (S27-05)", () => {
  it("parses JSON, an empty text removes the ceiling", () => {
    expect(parseCeiling("")).toEqual({ ceiling: null });
    expect(parseCeiling('{"budgets": {"ticket_usd": {"M": 20}}}')).toEqual({ ceiling: { budgets: { ticket_usd: { M: 20 } } } });
    expect("error" in parseCeiling("[1]")).toBe(true);
    expect("error" in parseCeiling("{oops")).toBe(true);
  });

  it("shows the ceiling and saves it", async () => {
    vi.mocked(api.policyCeiling).mockResolvedValue({ ceiling: { budgets: { ticket_usd: { M: 20 } } }, updated_by: "ada@varga.dev", updated_at: "2026-10-13T10:00:00" });
    vi.mocked(api.putPolicyCeiling).mockResolvedValue({ ceiling: null });
    wrap(<PolicyCeilingCard org="varga" canEdit />);
    const box = await screen.findByLabelText("Policy ceiling");
    await waitFor(() => expect((box as HTMLTextAreaElement).value).toContain('"M": 20'));
    expect(screen.getByText(/set by ada@varga.dev/)).toBeInTheDocument();
    fireEvent.change(box, { target: { value: '{"attempts": {"default_max": 2}}' } });
    fireEvent.click(screen.getByRole("button", { name: "Save the ceiling" }));
    await waitFor(() => expect(api.putPolicyCeiling).toHaveBeenCalledWith("varga", { attempts: { default_max: 2 } }));
  });

  it("is read-only for a member who is not an administrator", async () => {
    vi.mocked(api.policyCeiling).mockResolvedValue({ ceiling: null });
    wrap(<PolicyCeilingCard org="varga" canEdit={false} />);
    expect(await screen.findByLabelText("Policy ceiling")).toHaveAttribute("readonly");
    expect(screen.queryByRole("button", { name: "Save the ceiling" })).toBeNull();
  });
});
