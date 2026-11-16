import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import DevicePage from "@/app/device/page";
import { api, ApiError } from "@/lib/api";

/** Where a developer surface's sign-in is approved (S67-05): what is asking is shown before the person decides. */
const search = vi.hoisted(() => ({ value: "user_code=ABCD-EFGH" }));
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(search.value),
}));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: { ...real.api, deviceRequest: vi.fn(), decideDeviceRequest: vi.fn() },
  };
});

const asking = {
  user_code: "ABCD-EFGH",
  client_kind: "cli" as const,
  client_name: "choregos 0.27.8 on laptop",
  org: "acme",
  expires_at: "2026-11-16T14:00:00Z",
};

function show() {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <DevicePage />
    </QueryClientProvider>,
  );
}

describe("the device sign-in page", () => {
  beforeEach(() => {
    search.value = "user_code=ABCD-EFGH";
    vi.mocked(api.deviceRequest).mockReset().mockResolvedValue(asking);
    vi.mocked(api.decideDeviceRequest).mockReset().mockResolvedValue(undefined);
  });

  it("names what is asking, and nothing is approved before the click", async () => {
    show();
    expect(
      await screen.findByText(/choregos 0.27.8 on laptop \(the command line\)/),
    ).toBeTruthy();
    expect(screen.getByText("acme")).toBeTruthy();
    expect(api.decideDeviceRequest).not.toHaveBeenCalled();
  });

  it("approves the code the person was shown", async () => {
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    await waitFor(() =>
      expect(api.decideDeviceRequest).toHaveBeenCalledWith(
        "ABCD-EFGH",
        "approve",
      ),
    );
    expect(await screen.findByText(/it is signed in as you/)).toBeTruthy();
  });

  it("refuses on request", async () => {
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Refuse" }));
    await waitFor(() =>
      expect(api.decideDeviceRequest).toHaveBeenCalledWith("ABCD-EFGH", "deny"),
    );
    expect(await screen.findByText(/not signed in/)).toBeTruthy();
  });

  it("says so when the API refuses the approval", async () => {
    vi.mocked(api.decideDeviceRequest).mockRejectedValue(
      new ApiError(403, {
        title: "Forbidden",
        detail: "not that organisation's provider",
      }),
    );
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    expect(
      await screen.findByText(/not that organisation's provider/),
    ).toBeTruthy();
  });

  it("asks for the code when the link carries none, and when it is unknown", async () => {
    search.value = "";
    show();
    expect(screen.getByLabelText("Code")).toBeTruthy();
    expect(api.deviceRequest).not.toHaveBeenCalled();
  });

  it("an unknown or expired code is said, not shown as an empty request", async () => {
    vi.mocked(api.deviceRequest).mockRejectedValue(
      new ApiError(404, { title: "Not found" }),
    );
    show();
    expect(
      await screen.findByText(/unknown, expired or already used/),
    ).toBeTruthy();
  });
});
