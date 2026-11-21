import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import OrgConnectorsPage from "@/app/admin/connectors/page";
import { ProjectOperations } from "@/components/project-operations";
import { api } from "@/lib/api";
import type { OrgConnector, ProjectOperation } from "@/lib/types";
import { pageOf } from "./page";

const { params } = vi.hoisted(() => ({ params: new URLSearchParams() }));
vi.mock("next/navigation", () => ({ usePathname: () => "/admin/connectors", useSearchParams: () => params }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme" }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      orgConnectors: vi.fn(),
      projectOperations: vi.fn(),
      projects: vi.fn().mockResolvedValue({ items: [], meta: { has_more: false, next_cursor: null } }),
      connectors: vi.fn().mockResolvedValue([]),
      connectorTypes: vi.fn().mockResolvedValue([]),
    },
  };
});

const connector = (name: string) => ({ name, kind: "mcp", type: "mcp", operations: [] }) as unknown as OrgConnector;
const operation = (name: string) =>
  ({
    connector: "supplier",
    operation: name,
    access: "read",
    org_policy: "allowed",
    project_policy: null,
    effective_policy: "allowed",
  }) as unknown as ProjectOperation;

function show(ui: React.ReactElement) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui}</QueryClientProvider>,
  );
}

describe("the project's operations are read one page at a time (ADR 0206)", () => {
  beforeEach(() => vi.mocked(api.projectOperations).mockReset());

  it("asks for the next page with the cursor the server gave, and appends it", async () => {
    vi.mocked(api.projectOperations)
      .mockResolvedValueOnce(pageOf([operation("read_a")], "c1"))
      .mockResolvedValueOnce(pageOf([operation("read_b")]));
    show(<ProjectOperations slug="billing-api" />);
    expect(await screen.findByText("read_a")).toBeInTheDocument();
    expect(screen.queryByText("read_b")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    expect(await screen.findByText("read_b")).toBeInTheDocument();
    expect(screen.getByText("read_a")).toBeInTheDocument();
    expect(vi.mocked(api.projectOperations).mock.calls[1]![1]).toMatchObject({ cursor: "c1" });
    expect(screen.queryByRole("button", { name: "More" })).toBeNull();
  });
});

describe("the organisation's connectors are searched on the server (ADR 0206)", () => {
  beforeEach(() => vi.mocked(api.orgConnectors).mockReset());

  it("reads the next page on More, and sends the search text instead of filtering the first page", async () => {
    vi.mocked(api.orgConnectors).mockImplementation(async (_org, query) =>
      query?.q === "zeta"
        ? pageOf([connector("zeta-server")])
        : query?.cursor
          ? pageOf([connector("beta-server")])
          : pageOf([connector("alpha-server")], "next"),
    );
    show(<OrgConnectorsPage />);
    expect((await screen.findAllByText(/alpha-server/)).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    expect((await screen.findAllByText(/beta-server/)).length).toBeGreaterThan(0);

    const search = screen.getByRole("search");
    fireEvent.change(search.querySelector("input")!, { target: { value: "zeta" } });
    fireEvent.submit(search);
    expect((await screen.findAllByText(/zeta-server/)).length).toBeGreaterThan(0);
    await waitFor(() => expect(screen.queryAllByText(/alpha-server/)).toHaveLength(0));
    expect(vi.mocked(api.orgConnectors).mock.calls.some(([, query]) => query?.q === "zeta")).toBe(true);
  });
});
