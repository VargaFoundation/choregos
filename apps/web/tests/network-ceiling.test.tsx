import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NetworkCeilingCard } from "@/components/admin/network-ceiling";
import { api } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, policyCeiling: vi.fn(), putPolicyCeiling: vi.fn() } };
});

function wrap(node: React.ReactNode) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{node}</QueryClientProvider>);
}

const STORED = {
  budgets: { ticket_usd: { M: 20 } },
  attempts: { default_max: 2 },
  network: { allow_domains: ["github.com", "pypi.org"], deny_domains: ["news.ycombinator.com"], per_host_per_minute: 60, builtin_web_tools: "deny" as const },
};

describe("the organisation's network access (ADR 0070)", () => {
  beforeEach(() => {
    vi.mocked(api.policyCeiling).mockResolvedValue({ ceiling: STORED });
    vi.mocked(api.putPolicyCeiling).mockResolvedValue({ ceiling: null });
  });

  it("renders the stored section", async () => {
    wrap(<NetworkCeilingCard org="acme" canEdit />);
    await screen.findByDisplayValue("60");
    expect(screen.getByLabelText("Only these hosts", { exact: false })).toBeChecked();
    expect((screen.getByLabelText("Allowed hosts") as HTMLTextAreaElement).value).toBe("github.com\npypi.org");
    expect((screen.getByLabelText("Denied hosts") as HTMLTextAreaElement).value).toBe("news.ycombinator.com");
    expect((screen.getByLabelText("Requests per minute, per host") as HTMLInputElement).value).toBe("60");
    expect((screen.getByLabelText("Built-in web tools") as HTMLSelectElement).value).toBe("deny");
  });

  it("“Any public host” writes [\"*\"] and keeps every other key of the ceiling", async () => {
    wrap(<NetworkCeilingCard org="acme" canEdit />);
    await screen.findByDisplayValue("60");
    fireEvent.click(screen.getByLabelText("Any public host", { exact: false }));
    fireEvent.click(screen.getByRole("button", { name: "Save network access" }));
    await waitFor(() => expect(api.putPolicyCeiling).toHaveBeenCalled());
    const [org, sent] = vi.mocked(api.putPolicyCeiling).mock.calls[0]!;
    expect(org).toBe("acme");
    expect(sent).toMatchObject({ budgets: STORED.budgets, attempts: STORED.attempts });
    expect((sent as { network: Record<string, unknown> }).network).toMatchObject({
      allow_domains: ["*"],
      deny_domains: ["news.ycombinator.com"],
      per_host_per_minute: 60,
      builtin_web_tools: "deny",
    });
  });

  it("reads the ceiling again before writing: a change made elsewhere is not erased", async () => {
    wrap(<NetworkCeilingCard org="acme" canEdit />);
    await screen.findByDisplayValue("60");
    // The JSON card, or another administrator, changed the budgets meanwhile.
    vi.mocked(api.policyCeiling).mockResolvedValue({ ceiling: { ...STORED, budgets: { ticket_usd: { M: 5 } } } });
    fireEvent.change(screen.getByLabelText("Denied hosts"), { target: { value: "x.example\ny.example" } });
    fireEvent.click(screen.getByRole("button", { name: "Save network access" }));
    await waitFor(() => expect(api.putPolicyCeiling).toHaveBeenCalled());
    const sent = vi.mocked(api.putPolicyCeiling).mock.calls[0]![1] as { budgets: unknown; network: { deny_domains: string[] } };
    expect(sent.budgets).toEqual({ ticket_usd: { M: 5 } });
    expect(sent.network.deny_domains).toEqual(["x.example", "y.example"]);
  });

  it("“No cap” writes null, and an empty rate or contact writes null", async () => {
    wrap(<NetworkCeilingCard org="acme" canEdit />);
    await screen.findByDisplayValue("60");
    fireEvent.click(screen.getByLabelText("No cap", { exact: false }));
    fireEvent.change(screen.getByLabelText("Requests per minute, per host"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Built-in web tools"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save network access" }));
    await waitFor(() => expect(api.putPolicyCeiling).toHaveBeenCalled());
    const network = (vi.mocked(api.putPolicyCeiling).mock.calls[0]![1] as { network: Record<string, unknown> }).network;
    expect(network).toMatchObject({ allow_domains: null, per_host_per_minute: null, builtin_web_tools: null, user_agent_contact: null });
  });

  it("flags a bad pattern before sending anything", async () => {
    wrap(<NetworkCeilingCard org="acme" canEdit />);
    await screen.findByDisplayValue("60");
    fireEvent.change(screen.getByLabelText("Denied hosts"), { target: { value: "https://x.example\n10.0.0.1" } });
    fireEvent.click(screen.getByRole("button", { name: "Save network access" }));
    expect(await screen.findByText(/https:\/\/x\.example.*scheme/)).toBeInTheDocument();
    expect(screen.getByText(/10\.0\.0\.1.*IP address/)).toBeInTheDocument();
    expect(api.putPolicyCeiling).not.toHaveBeenCalled();
  });

  it("refuses an empty list of allowed hosts", async () => {
    wrap(<NetworkCeilingCard org="acme" canEdit />);
    await screen.findByDisplayValue("60");
    fireEvent.change(screen.getByLabelText("Allowed hosts"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save network access" }));
    expect(await screen.findByText(/Name at least one allowed host/)).toBeInTheDocument();
    expect(api.putPolicyCeiling).not.toHaveBeenCalled();
  });

  it("shows the server's refusal", async () => {
    vi.mocked(api.putPolicyCeiling).mockImplementation(() => Promise.reject(new Error("`x` is not a host pattern")));
    wrap(<NetworkCeilingCard org="acme" canEdit />);
    fireEvent.click(await screen.findByRole("button", { name: "Save network access" }));
    expect(await screen.findByText(/not a host pattern/)).toBeInTheDocument();
  });

  it("is read-only for a member who is not an administrator", async () => {
    wrap(<NetworkCeilingCard org="acme" canEdit={false} />);
    expect(await screen.findByLabelText("Denied hosts")).toHaveAttribute("readonly");
    expect(screen.queryByRole("button", { name: "Save network access" })).toBeNull();
    expect(screen.getByLabelText("No cap", { exact: false })).toBeDisabled();
  });
});
