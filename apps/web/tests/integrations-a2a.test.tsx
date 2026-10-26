import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { A2aAgent } from "@/components/integrations/a2a";
import { api } from "@/lib/api";
import { readFileSync } from "node:fs";
import path from "node:path";
import {
  A2A_MAX_FILE_BYTES_CEILING,
  A2A_MAX_FILE_BYTES_DEFAULT,
  a2aCardUrl,
  a2aFileLimit,
  formatMiB,
} from "@/lib/integrations";
import type { ApiTokenCreated, Integrations, ProjectPage } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, integrations: vi.fn(), createToken: vi.fn(), projects: vi.fn() } };
});

const INFOS: Integrations = {
  mcp_url: "https://choregos.example/mcp",
  protocol_versions: ["2025-06-18"],
  oauth: { enabled: false },
  version: "0.16.3",
};

const PROJECTS = {
  items: [
    { id: "p1", slug: "billing-api", org: "varga", name: "Billing API", status: "active", config: {} },
    {
      id: "p2",
      slug: "contracts",
      org: "varga",
      name: "Contracts",
      status: "active",
      config: { a2a: { max_file_bytes: 25 * 1024 * 1024 } },
    },
  ],
  meta: { total: 2 },
} as unknown as ProjectPage;

describe("the A2A card on the Integrations page (ADR 0078)", () => {
  it("is beside /mcp, under the organisation", () => {
    expect(a2aCardUrl("https://choregos.example/mcp/", "varga")).toBe(
      "https://choregos.example/a2a/orgs/varga/.well-known/agent-card.json",
    );
  });

  it("shows the card, the endpoint, and creates a token of the a2a scope only", async () => {
    vi.mocked(api.integrations).mockResolvedValue(INFOS);
    vi.mocked(api.projects).mockResolvedValue(PROJECTS);
    vi.mocked(api.createToken).mockResolvedValue({ token: "chg_a2a_once" } as ApiTokenCreated);
    render(
      <QueryClientProvider client={new QueryClient()}>
        <A2aAgent org="varga" />
      </QueryClientProvider>,
    );
    expect(await screen.findByText("https://choregos.example/a2a/orgs/varga/.well-known/agent-card.json")).toBeInTheDocument();
    expect(screen.getByTestId("a2a-endpoint")).toHaveTextContent("https://choregos.example/a2a/orgs/varga");
    fireEvent.click(screen.getByRole("button", { name: "Create an A2A token" }));
    expect(await screen.findByTestId("a2a-token")).toHaveTextContent("chg_a2a_once");
    expect(vi.mocked(api.createToken).mock.calls[0]![0].scopes).toEqual(["a2a"]);
  });

  it("says what each project takes in files, and links to where it is changed", async () => {
    vi.mocked(api.integrations).mockResolvedValue(INFOS);
    vi.mocked(api.projects).mockResolvedValue(PROJECTS);
    render(
      <QueryClientProvider client={new QueryClient()}>
        <A2aAgent org="varga" />
      </QueryClientProvider>,
    );
    const limits = await screen.findByTestId("a2a-file-limits");
    const rows = within(limits).getAllByRole("listitem");
    expect(rows.map((row) => row.textContent)).toEqual([
      "Billing API: up to 10 MiB change it in its settings",
      "Contracts: up to 25 MiB change it in its settings",
    ]);
    expect(within(rows[1]!).getByRole("link")).toHaveAttribute("href", "/p/contracts/settings#a2a");
  });
});

describe("the A2A file limit the console shows is the door's", () => {
  it("has the schema's default and ceiling", () => {
    const schema = JSON.parse(
      readFileSync(path.resolve(__dirname, "../../../packages/contracts/schemas/project.schema.json"), "utf8"),
    );
    const field = schema.properties.a2a.properties.max_file_bytes;
    expect(field.default).toBe(A2A_MAX_FILE_BYTES_DEFAULT);
    expect(field.maximum).toBe(A2A_MAX_FILE_BYTES_CEILING);
  });

  it("reads the project's value, or the default", () => {
    expect(a2aFileLimit({})).toBe(A2A_MAX_FILE_BYTES_DEFAULT);
    expect(a2aFileLimit({ a2a: { max_file_bytes: 2048 } })).toBe(2048);
    expect(formatMiB(10 * 1024 * 1024)).toBe("10 MiB");
    expect(formatMiB(1.5 * 1024 * 1024)).toBe("1.5 MiB");
    expect(formatMiB(2048)).toBe("2 KiB");
  });
});
