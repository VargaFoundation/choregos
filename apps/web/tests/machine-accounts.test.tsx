import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MachineAccounts } from "@/components/project-settings/machine-accounts";
import { api } from "@/lib/api";
import type { ApiTokenCreated, MachineAccount } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      machineAccounts: vi.fn(),
      createMachineAccount: vi.fn(),
      removeMachineAccount: vi.fn(),
      createMachineToken: vi.fn(),
      revokeMachineToken: vi.fn(),
    },
  };
});

const account = (extra: Partial<MachineAccount> = {}): MachineAccount => ({
  slug: "partner-app",
  display_name: "Partner app",
  address: "partner-app.p1@machines.choregos.invalid",
  writes_per_day: 500,
  created_at: "2026-11-09T10:00:00Z",
  tokens: [],
  ...extra,
});

function view() {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MachineAccounts slug="billing-api" />
    </QueryClientProvider>,
  );
}

describe("a project's applications (ADR 0131)", () => {
  beforeEach(() => vi.clearAllMocks());

  it("says when there is none, and creates one under a valid name only", async () => {
    vi.mocked(api.machineAccounts).mockResolvedValue([]);
    vi.mocked(api.createMachineAccount).mockResolvedValue(account());
    view();
    expect(await screen.findByText("No application yet.")).toBeInTheDocument();
    const create = screen.getByRole("button", { name: "Create" });
    fireEvent.change(screen.getByLabelText(/New application/), {
      target: { value: "Not Valid" },
    });
    expect(create).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/New application/), {
      target: { value: "partner-app" },
    });
    fireEvent.click(create);
    await vi.waitFor(() =>
      expect(api.createMachineAccount).toHaveBeenCalledWith("billing-api", {
        slug: "partner-app",
      }),
    );
  });

  it("shows an issued token once, and never one it only lists", async () => {
    vi.mocked(api.machineAccounts).mockResolvedValue([
      account({
        tokens: [
          {
            id: "t1",
            name: "first",
            created_at: "2026-11-09T10:00:00Z",
            scopes: ["a2a"],
            expires_at: "2027-02-07T10:00:00Z",
          },
        ],
      }),
    ]);
    vi.mocked(api.createMachineToken).mockResolvedValue({
      token: "chg_once",
    } as ApiTokenCreated);
    view();
    const line = await screen.findByTestId("machine-account-partner-app");
    expect(line).toHaveTextContent("first · expires 2027-02-07");
    expect(screen.queryByTestId("machine-token-partner-app")).toBeNull();
    fireEvent.click(
      within(line).getByRole("button", { name: "Issue a token" }),
    );
    expect(
      await screen.findByTestId("machine-token-partner-app"),
    ).toHaveTextContent("chg_once");
  });

  it("asks before removing an application", async () => {
    vi.mocked(api.machineAccounts).mockResolvedValue([account()]);
    vi.mocked(api.removeMachineAccount).mockResolvedValue(undefined);
    view();
    fireEvent.click(await screen.findByRole("button", { name: "Remove" }));
    expect(api.removeMachineAccount).not.toHaveBeenCalled();
    fireEvent.click(
      await screen.findByRole("button", { name: "Remove the application" }),
    );
    await vi.waitFor(() =>
      expect(api.removeMachineAccount).toHaveBeenCalledWith(
        "billing-api",
        "partner-app",
      ),
    );
  });

  it("an answer that is not a list shows no application instead of breaking the page", async () => {
    vi.mocked(api.machineAccounts).mockResolvedValue({} as never);
    view();
    expect(
      await screen.findByTestId("machine-accounts-none"),
    ).toBeInTheDocument();
  });
});
