import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import OrgConnectorsPage from "@/app/admin/connectors/page";
import type { OrgConnector } from "@/lib/types";

vi.mock("next/navigation", () => ({ usePathname: () => "/admin/connectors" }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "varga" }) }));

const { pare_feu, miseAJour } = vi.hoisted(() => ({
  pare_feu: {
    name: "pfsense",
    kind: "firewall",
    type: "pfsense",
    operations: [
      { name: "list_aliases", access: "read", policy: "allowed", groups: [] },
      { name: "update_firewall_rule", access: "write", policy: "approval", groups: [], approver_group: "netops-approvers" },
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
      orgConnectors: vi.fn().mockResolvedValue([pare_feu]),
      projects: vi.fn().mockResolvedValue({ items: [], meta: { has_more: false } }),
      connectors: vi.fn().mockResolvedValue([]),
      connectorTypes: vi.fn().mockResolvedValue([]),
      updateConnectorOperation: miseAJour,
    },
  };
});

describe("le groupe qui approuve une opération (ADR 0052)", () => {
  it("se règle sur une écriture seulement, et se vide pour rendre la décision aux propriétaires", async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <OrgConnectorsPage />
      </QueryClientProvider>,
    );
    const table = await screen.findByTestId("operations-pfsense");
    expect(within(table).getByText("A read: nobody approves it")).toBeInTheDocument();
    expect(within(table).queryByLabelText("Approver group of list_aliases")).toBeNull();

    const champ = within(table).getByLabelText("Approver group of update_firewall_rule");
    fireEvent.change(champ, { target: { value: "other-approvers" } });
    const [valider] = within(champ.closest("form") as HTMLElement).getAllByRole("button");
    fireEvent.click(valider!);
    await waitFor(() =>
      expect(miseAJour).toHaveBeenCalledWith("varga", "pfsense", "update_firewall_rule", {
        approver_group: "other-approvers",
      }),
    );

    fireEvent.change(champ, { target: { value: "  " } });
    fireEvent.click(valider!);
    await waitFor(() => expect(miseAJour).toHaveBeenLastCalledWith("varga", "pfsense", "update_firewall_rule", {
      approver_group: null,
    }));
  });
});
