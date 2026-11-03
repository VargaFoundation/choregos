import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TrackerIdentities } from "@/components/admin/tracker-identities";
import { api, ApiError } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      trackerIdentities: vi.fn(),
      setTrackerIdentity: vi.fn(),
      deleteTrackerIdentity: vi.fn(),
    },
  };
});

const LINK = { tracker: "github", external_id: "1001", login: "mallory", user_id: "u2", created_by: null, created_at: "" } as const;

function wrap() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <TrackerIdentities org="acme" userId="u2" />
    </QueryClientProvider>,
  );
}

describe("tracker identities", () => {
  beforeEach(() => {
    cleanup();
    vi.resetAllMocks();
  });

  it("sets and removes a GitHub link", async () => {
    vi.mocked(api.trackerIdentities).mockResolvedValue([]);
    vi.mocked(api.setTrackerIdentity).mockResolvedValue(LINK);
    vi.mocked(api.deleteTrackerIdentity).mockResolvedValue(undefined);
    wrap();
    fireEvent.change(await screen.findByLabelText("GitHub login"), { target: { value: "mallory" } });
    fireEvent.change(screen.getByLabelText("GitHub user id"), { target: { value: "10a01" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(api.setTrackerIdentity).toHaveBeenCalledWith("acme", "u2", "github", { external_id: "1001", login: "mallory" }),
    );
    cleanup();
    vi.mocked(api.trackerIdentities).mockResolvedValue([LINK]);
    wrap();
    fireEvent.click(await screen.findByRole("button", { name: "Remove" }));
    await waitFor(() => expect(api.deleteTrackerIdentity).toHaveBeenCalledWith("acme", "u2", "github"));
  });

  it("shows the conflict when the id belongs to another member", async () => {
    vi.mocked(api.trackerIdentities).mockResolvedValue([]);
    vi.mocked(api.setTrackerIdentity).mockRejectedValue(
      new ApiError(409, { title: "Conflict", detail: "GitHub user 1001 is already linked to another member" }),
    );
    wrap();
    fireEvent.change(await screen.findByLabelText("GitHub login"), { target: { value: "mallory" } });
    fireEvent.change(screen.getByLabelText("GitHub user id"), { target: { value: "1001" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("GitHub user 1001 is already linked to another member")).toBeTruthy();
  });
});
