import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PolicyCeilingCard, parseCeiling } from "@/components/admin/policy-ceiling";
import { ApiError, api } from "@/lib/api";

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

  it("still saves the raw JSON, under Advanced", async () => {
    vi.mocked(api.policyCeiling).mockResolvedValue({ ceiling: { budgets: { ticket_usd: { M: 20 } } }, updated_by: "ada@varga.dev", updated_at: "2026-10-13T10:00:00" });
    vi.mocked(api.putPolicyCeiling).mockResolvedValue({ ceiling: null });
    wrap(<PolicyCeilingCard org="varga" canEdit />);
    const box = await screen.findByLabelText("Policy ceiling");
    await waitFor(() => expect((box as HTMLTextAreaElement).value).toContain('"M": 20'));
    expect(screen.getByText(/set by ada@varga.dev/)).toBeInTheDocument();
    fireEvent.change(box, { target: { value: '{"attempts": {"default_max": 2}}' } });
    fireEvent.click(screen.getByRole("button", { name: "Save the raw JSON" }));
    await waitFor(() => expect(api.putPolicyCeiling).toHaveBeenCalledWith("varga", { attempts: { default_max: 2 } }));
  });

  const STORED = {
    budgets: { ticket_usd: { M: 20 }, max_turns: { default: 60 } },
    attempts: { default_max: 2 },
    network: { allow_domains: null, deny_domains: ["evil.example"], per_host_per_minute: 60 },
    future_section: { keep: true },
  };

  it("prefills the form from the stored ceiling", async () => {
    vi.mocked(api.policyCeiling).mockResolvedValue({ ceiling: STORED, updated_by: "ada@varga.dev", updated_at: "2026-10-13T10:00:00" });
    wrap(<PolicyCeilingCard org="varga" canEdit />);
    await waitFor(() => expect(screen.getByLabelText("Ticket budget M")).toHaveValue("20"));
    expect(screen.getByLabelText("Max turns default")).toHaveValue("60");
    expect(screen.getByLabelText("Default attempts")).toHaveValue("2");
    expect(screen.getByLabelText("Ticket budget S")).toHaveValue("");
    expect(screen.getByLabelText("Ticket budget S")).toHaveAttribute("placeholder", "platform: 8");
  });

  it("sends the edited fields and keeps the network and unknown keys the ceiling already had", async () => {
    vi.mocked(api.policyCeiling).mockResolvedValue({ ceiling: STORED });
    vi.mocked(api.putPolicyCeiling).mockResolvedValue({ ceiling: null });
    wrap(<PolicyCeilingCard org="varga" canEdit />);
    await waitFor(() => expect(screen.getByLabelText("Ticket budget M")).toHaveValue("20"));
    fireEvent.change(screen.getByLabelText("Ticket budget M"), { target: { value: "15" } });
    fireEvent.change(screen.getByLabelText("Max turns default"), { target: { value: "40" } });
    fireEvent.click(screen.getByRole("button", { name: "Save the ceiling" }));
    await waitFor(() =>
      expect(api.putPolicyCeiling).toHaveBeenCalledWith("varga", {
        ...STORED,
        budgets: { ticket_usd: { M: 15 }, max_turns: { default: 40 } },
      }),
    );
  });

  it("sends exactly the two edited fields when the stored ceiling was empty", async () => {
    vi.mocked(api.policyCeiling).mockResolvedValue({ ceiling: null });
    vi.mocked(api.putPolicyCeiling).mockResolvedValue({ ceiling: null });
    wrap(<PolicyCeilingCard org="varga" canEdit />);
    await screen.findByLabelText("Ticket budget M");
    fireEvent.change(screen.getByLabelText("Ticket budget M"), { target: { value: "12.5" } });
    fireEvent.change(screen.getByLabelText("Max turns default"), { target: { value: "30" } });
    fireEvent.click(screen.getByRole("button", { name: "Save the ceiling" }));
    await waitFor(() =>
      expect(api.putPolicyCeiling).toHaveBeenCalledWith("varga", { budgets: { ticket_usd: { M: 12.5 }, max_turns: { default: 30 } } }),
    );
  });

  it("omits an emptied field, and the section it leaves empty", async () => {
    vi.mocked(api.policyCeiling).mockResolvedValue({ ceiling: STORED });
    vi.mocked(api.putPolicyCeiling).mockResolvedValue({ ceiling: null });
    wrap(<PolicyCeilingCard org="varga" canEdit />);
    await waitFor(() => expect(screen.getByLabelText("Default attempts")).toHaveValue("2"));
    fireEvent.change(screen.getByLabelText("Default attempts"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Max turns default"), { target: { value: " " } });
    fireEvent.click(screen.getByRole("button", { name: "Save the ceiling" }));
    await waitFor(() =>
      expect(api.putPolicyCeiling).toHaveBeenCalledWith("varga", {
        budgets: { ticket_usd: { M: 20 } },
        network: STORED.network,
        future_section: STORED.future_section,
      }),
    );
  });

  it("reads the ceiling again before saving, so a change made elsewhere is not erased", async () => {
    vi.mocked(api.policyCeiling).mockResolvedValueOnce({ ceiling: null });
    vi.mocked(api.policyCeiling).mockResolvedValue({ ceiling: { network: { deny_domains: ["x.example"] } } as never }); // the generated type lacks `network` (contract drift)
    vi.mocked(api.putPolicyCeiling).mockResolvedValue({ ceiling: null });
    wrap(<PolicyCeilingCard org="varga" canEdit />);
    await screen.findByLabelText("Ticket budget M");
    fireEvent.change(screen.getByLabelText("Findings per run"), { target: { value: "0" } });
    fireEvent.click(screen.getByRole("button", { name: "Save the ceiling" }));
    await waitFor(() =>
      expect(api.putPolicyCeiling).toHaveBeenCalledWith("varga", { findings: { max_per_run: 0 }, network: { deny_domains: ["x.example"] } }),
    );
  });

  it("blocks the save below a minimum and says which field", async () => {
    vi.mocked(api.policyCeiling).mockResolvedValue({ ceiling: null });
    vi.mocked(api.putPolicyCeiling).mockReset();
    wrap(<PolicyCeilingCard org="varga" canEdit />);
    await screen.findByLabelText("Max turns default");
    fireEvent.change(screen.getByLabelText("Max turns default"), { target: { value: "0" } });
    fireEvent.change(screen.getByLabelText("Daily spend per project"), { target: { value: "0" } });
    fireEvent.change(screen.getByLabelText("Files in a diff"), { target: { value: "2.5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save the ceiling" }));
    expect(await screen.findByText("Must be at least 1")).toBeInTheDocument();
    expect(screen.getByText("Must be more than 0")).toBeInTheDocument();
    expect(screen.getByText("Enter a whole number")).toBeInTheDocument();
    expect(screen.getByLabelText("Max turns default")).toHaveAttribute("aria-invalid", "true");
    expect(api.putPolicyCeiling).not.toHaveBeenCalled();
    // 0 is a valid cap where the schema says ge=0.
    fireEvent.change(screen.getByLabelText("Max turns default"), { target: { value: "5" } });
    fireEvent.change(screen.getByLabelText("Daily spend per project"), { target: { value: "5" } });
    fireEvent.change(screen.getByLabelText("Files in a diff"), { target: { value: "5" } });
    fireEvent.change(screen.getByLabelText("Findings per run"), { target: { value: "0" } });
    expect(screen.queryByText("Must be at least 1")).toBeNull();
  });

  it("puts a server 422 on the field it names", async () => {
    vi.mocked(api.policyCeiling).mockResolvedValue({ ceiling: null });
    vi.mocked(api.putPolicyCeiling).mockRejectedValue(
      new ApiError(422, { title: "Unprocessable", detail: "not a policy ceiling", errors: [{ loc: ["ceiling", "budgets", "ticket_usd", "M"], msg: "refused by the server" }, { loc: ["ceiling", "network", "x"], msg: "odd" }] }),
    );
    wrap(<PolicyCeilingCard org="varga" canEdit />);
    await screen.findByLabelText("Ticket budget M");
    fireEvent.change(screen.getByLabelText("Ticket budget M"), { target: { value: "9" } });
    fireEvent.click(screen.getByRole("button", { name: "Save the ceiling" }));
    expect(await screen.findByText("refused by the server")).toBeInTheDocument();
    expect(screen.getByLabelText("Ticket budget M")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText("network.x: odd")).toBeInTheDocument();
  });

  it("is read-only for a member who is not an administrator", async () => {
    vi.mocked(api.policyCeiling).mockResolvedValue({ ceiling: null });
    wrap(<PolicyCeilingCard org="varga" canEdit={false} />);
    expect(await screen.findByLabelText("Policy ceiling")).toHaveAttribute("readonly");
    expect(screen.getByLabelText("Ticket budget M")).toHaveAttribute("readonly");
    expect(screen.queryByRole("button", { name: "Save the ceiling" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Save the raw JSON" })).toBeNull();
  });
});
