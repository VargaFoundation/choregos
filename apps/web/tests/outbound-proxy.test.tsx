import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { OutboundProxyCard } from "@/components/admin/outbound-proxy";
import { api, ApiError } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: { ...real.api, outboundProxy: vi.fn(), putOutboundProxy: vi.fn(), deleteOutboundProxy: vi.fn(), testOutboundProxy: vi.fn() },
  };
});

function wrap(node: React.ReactNode) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{node}</QueryClientProvider>);
}

const STORED = {
  proxy: {
    enabled: true,
    endpoint: "gate.example.com:7000",
    scheme: "http" as const,
    username: "user-acme-country-fr",
    password_set: true,
    applies_to: ["web_fetch" as const, "web_search" as const],
  },
  updated_by: "lea@varga.dev",
  updated_at: "2026-10-26T10:00:00Z",
};
const NONE = { proxy: null, updated_by: null, updated_at: null };

const save = () => screen.getByRole("button", { name: "Save outbound proxy" });

describe("the organisation's outbound proxy (ADR 0087)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(api.outboundProxy).mockResolvedValue(STORED);
    vi.mocked(api.putOutboundProxy).mockResolvedValue(STORED);
    vi.mocked(api.deleteOutboundProxy).mockResolvedValue(undefined);
  });

  it("renders the stored setting; the password field is empty and says it is set", async () => {
    wrap(<OutboundProxyCard org="acme" canEdit />);
    await screen.findByDisplayValue("gate.example.com:7000");
    expect(screen.getByLabelText("Username")).toHaveValue("user-acme-country-fr");
    const password = screen.getByLabelText("Password") as HTMLInputElement;
    expect(password.type).toBe("password");
    expect(password.value).toBe("");
    expect(screen.getByText("set")).toBeInTheDocument();
    expect(screen.getByLabelText(/Web reading/)).toBeChecked();
    expect(screen.getByLabelText(/Knowledge-base/)).not.toBeChecked();
  });

  it("an empty password is not sent: the stored one is kept", async () => {
    wrap(<OutboundProxyCard org="acme" canEdit />);
    await screen.findByDisplayValue("gate.example.com:7000");
    fireEvent.change(screen.getByLabelText("Username"), { target: { value: "other" } });
    fireEvent.click(save());
    await waitFor(() => expect(api.putOutboundProxy).toHaveBeenCalled());
    const sent = vi.mocked(api.putOutboundProxy).mock.calls[0]![1];
    expect(sent).not.toHaveProperty("password");
    expect(sent).toMatchObject({ endpoint: "gate.example.com:7000", username: "other", applies_to: ["web_fetch", "web_search"] });
  });

  it("a typed password is sent", async () => {
    wrap(<OutboundProxyCard org="acme" canEdit />);
    await screen.findByDisplayValue("gate.example.com:7000");
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "s3cret" } });
    fireEvent.click(save());
    await waitFor(() => expect(api.putOutboundProxy).toHaveBeenCalled());
    expect(vi.mocked(api.putOutboundProxy).mock.calls[0]![1]).toMatchObject({ password: "s3cret" });
  });

  it("the first time, the password is required and Save stays disabled without it", async () => {
    vi.mocked(api.outboundProxy).mockResolvedValue(NONE);
    wrap(<OutboundProxyCard org="acme" canEdit />);
    await screen.findByLabelText("Endpoint");
    fireEvent.change(screen.getByLabelText("Endpoint"), { target: { value: "gate.example.com:7000" } });
    expect(save()).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "pw" } });
    expect(save()).toBeEnabled();
  });

  it("flags a bad endpoint and keeps Save disabled until it is host:port", async () => {
    wrap(<OutboundProxyCard org="acme" canEdit />);
    await screen.findByDisplayValue("gate.example.com:7000");
    for (const bad of ["https://gate.example.com:7000", "user:pw@gate.example.com:7000", "gate.example.com", "gate.example.com:70000"]) {
      fireEvent.change(screen.getByLabelText("Endpoint"), { target: { value: bad } });
      expect(save()).toBeDisabled();
      expect(screen.getByText(/host:port|between 1 and 65535/, { selector: "p" })).toBeInTheDocument();
    }
    fireEvent.change(screen.getByLabelText("Endpoint"), { target: { value: "gate.example.com:7000" } });
    expect(save()).toBeEnabled();
  });

  it("Save is disabled when no kind of traffic is chosen", async () => {
    wrap(<OutboundProxyCard org="acme" canEdit />);
    await screen.findByDisplayValue("gate.example.com:7000");
    fireEvent.click(screen.getByLabelText(/Web reading/));
    fireEvent.click(screen.getByLabelText(/Web search/));
    expect(save()).toBeDisabled();
  });

  it("says search_engines needs the deployment's relay", async () => {
    wrap(<OutboundProxyCard org="acme" canEdit />);
    await screen.findByDisplayValue("gate.example.com:7000");
    expect(screen.getByText(/only where the deployment runs the relay/)).toBeInTheDocument();
  });

  it("says where the provider's options go", async () => {
    wrap(<OutboundProxyCard org="acme" canEdit />);
    await screen.findByDisplayValue("gate.example.com:7000");
    expect(screen.getByText(/as your provider documents them/)).toBeInTheDocument();
  });

  it("shows the API's refusal as returned", async () => {
    vi.mocked(api.putOutboundProxy).mockRejectedValue(new ApiError(409, { title: "Conflict", detail: "this deployment has no secrets key" }));
    wrap(<OutboundProxyCard org="acme" canEdit />);
    await screen.findByDisplayValue("gate.example.com:7000");
    fireEvent.click(save());
    expect(await screen.findByText("this deployment has no secrets key")).toBeInTheDocument();
  });

  it("Test shows the exit IP, the country and the time", async () => {
    vi.mocked(api.testOutboundProxy).mockResolvedValue({ ok: true, exit_ip: "203.0.113.42", country: "FR", error: null, elapsed_ms: 640, target: "https://ipinfo.io/json" });
    wrap(<OutboundProxyCard org="acme" canEdit />);
    await screen.findByDisplayValue("gate.example.com:7000");
    fireEvent.click(screen.getByRole("button", { name: "Test the proxy" }));
    const line = await screen.findByRole("status");
    expect(line).toHaveTextContent("203.0.113.42");
    expect(line).toHaveTextContent("FR");
    expect(line).toHaveTextContent("640 ms");
  });

  it("Test shows a failing proxy's error (200 with ok: false) and an HTTP error", async () => {
    vi.mocked(api.testOutboundProxy).mockResolvedValueOnce({ ok: false, exit_ip: null, country: null, error: "proxy gate.example.com:7000 answered 407", elapsed_ms: 12, target: "x" });
    wrap(<OutboundProxyCard org="acme" canEdit />);
    await screen.findByDisplayValue("gate.example.com:7000");
    fireEvent.click(screen.getByRole("button", { name: "Test the proxy" }));
    expect(await screen.findByText(/answered 407/)).toBeInTheDocument();
    expect(screen.queryByRole("status")).toBeNull();
    vi.mocked(api.testOutboundProxy).mockRejectedValueOnce(new ApiError(404, { title: "Not Found", detail: "no outbound proxy is set" }));
    fireEvent.click(screen.getByRole("button", { name: "Test the proxy" }));
    expect(await screen.findByText("no outbound proxy is set")).toBeInTheDocument();
  });

  it("Remove asks for confirmation, and deletes only once confirmed", async () => {
    wrap(<OutboundProxyCard org="acme" canEdit />);
    await screen.findByDisplayValue("gate.example.com:7000");
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(api.deleteOutboundProxy).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(api.deleteOutboundProxy).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove the proxy" }));
    await waitFor(() => expect(api.deleteOutboundProxy).toHaveBeenCalledWith("acme"));
  });

  it("a non-administrator sees no section and triggers no read", () => {
    const { container } = wrap(<OutboundProxyCard org="acme" canEdit={false} />);
    expect(container).toBeEmptyDOMElement();
    expect(api.outboundProxy).not.toHaveBeenCalled();
  });
});
