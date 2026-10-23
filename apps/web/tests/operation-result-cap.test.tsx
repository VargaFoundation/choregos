import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import OrgConnectorsPage from "@/app/admin/connectors/page";
import type { OrgConnector } from "@/lib/types";

vi.mock("next/navigation", () => ({ usePathname: () => "/admin/connectors", useSearchParams: () => new URLSearchParams() }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "varga" }) }));

const { journaux, miseAJour } = vi.hoisted(() => ({
  journaux: {
    name: "logs",
    kind: "mcp",
    type: "mcp",
    operations: [
      { name: "read_logs", access: "read", policy: "allowed", groups: [], result_max_bytes: 262144 },
      { name: "lookup", access: "read", policy: "allowed", groups: [] },
    ],
  } as unknown as OrgConnector,
  miseAJour: vi.fn().mockResolvedValue({}),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
    api: {
      ...reel.api,
      orgConnectors: vi.fn().mockResolvedValue([journaux]),
      projects: vi.fn().mockResolvedValue({ items: [], meta: { has_more: false } }),
      connectors: vi.fn().mockResolvedValue([]),
      connectorTypes: vi.fn().mockResolvedValue([]),
      updateConnectorOperation: miseAJour,
    },
  };
});

function submitOf(field: HTMLElement): HTMLButtonElement {
  return within(field.closest("form") as HTMLElement).getByRole("button") as HTMLButtonElement;
}

describe("the result cap of an operation (S40-17b)", () => {
  it("shows the stored cap, sends a new one, and clears it back to the platform's default", async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <OrgConnectorsPage />
      </QueryClientProvider>,
    );
    const table = await screen.findByTestId("operations-logs");
    const stored = within(table).getByLabelText("Result cap of read_logs") as HTMLInputElement;
    expect(stored.value).toBe("262144");
    expect(submitOf(stored)).toBeDisabled();

    const empty = within(table).getByLabelText("Result cap of lookup") as HTMLInputElement;
    expect(empty.value).toBe("");
    expect(empty.placeholder).toBe("Platform default");

    fireEvent.change(empty, { target: { value: "4096" } });
    fireEvent.click(submitOf(empty));
    await waitFor(() =>
      expect(miseAJour).toHaveBeenCalledWith("varga", "logs", "lookup", { result_max_bytes: 4096 }),
    );

    fireEvent.change(stored, { target: { value: " " } });
    fireEvent.click(submitOf(stored));
    await waitFor(() =>
      expect(miseAJour).toHaveBeenLastCalledWith("varga", "logs", "read_logs", { result_max_bytes: null }),
    );
  });

  it("does not send a cap below 1024 bytes or one that is not a whole number", async () => {
    miseAJour.mockClear();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <OrgConnectorsPage />
      </QueryClientProvider>,
    );
    const table = await screen.findByTestId("operations-logs");
    const field = within(table).getByLabelText("Result cap of lookup");
    for (const bad of ["512", "2048.5", "64k"]) {
      fireEvent.change(field, { target: { value: bad } });
      expect(submitOf(field)).toBeDisabled();
      expect(within(table).getByText("at least 1024 bytes")).toBeInTheDocument();
      fireEvent.submit(field.closest("form") as HTMLElement);
    }
    expect(miseAJour).not.toHaveBeenCalled();
  });
});
