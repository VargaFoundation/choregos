import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { InstanceIdentityCard } from "@/components/admin/instance-identity";
import { ApiError, api } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, platformIdentity: vi.fn() } };
});

function wrap(node: React.ReactNode) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{node}</QueryClientProvider>);
}

describe("how people sign in to this instance (S42-03)", () => {
  it("renders the rows and the read-only note", async () => {
    vi.mocked(api.platformIdentity).mockResolvedValue({
      items: [
        { key: "oidc.issuer", label: "OIDC issuer", value: "https://sso.example.test/realms/x", source: "settings" },
        { key: "oidc.client_secret", label: "OIDC client secret", value: "set", source: "settings" },
      ],
    });
    wrap(<InstanceIdentityCard />);
    expect(await screen.findByText("https://sso.example.test/realms/x")).toBeInTheDocument();
    expect(screen.getByText("OIDC issuer")).toBeInTheDocument();
    expect(screen.getByText("set")).toBeInTheDocument();
    expect(screen.queryByText("missing")).toBeNull();
    expect(screen.getByText(/read-only here/)).toBeInTheDocument();
  });

  it("flags a missing secret with a badge", async () => {
    vi.mocked(api.platformIdentity).mockResolvedValue({
      items: [{ key: "oidc.client_secret", label: "OIDC client secret", value: "missing", source: "default" }],
    });
    wrap(<InstanceIdentityCard />);
    expect(await screen.findByText("missing")).toBeInTheDocument();
  });

  it("is hidden for a reader who is not a platform administrator (403)", async () => {
    vi.mocked(api.platformIdentity).mockRejectedValue(new ApiError(403, { title: "Forbidden" }));
    const { container } = wrap(<InstanceIdentityCard />);
    await waitFor(() => expect(api.platformIdentity).toHaveBeenCalled());
    await waitFor(() => expect(container.querySelector("table")).toBeNull());
    expect(screen.queryByText("How people sign in to this instance")).toBeNull();
  });
});
