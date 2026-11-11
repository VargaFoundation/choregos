import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import path from "node:path";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DocsProvider } from "@/components/docs-link";
import { A2aIntegration } from "@/components/integrations/a2a";
import { IntegrationsPage } from "@/components/integrations/page";
import { api } from "@/lib/api";
import {
  A2A_MAX_FILE_BYTES_CEILING,
  A2A_MAX_FILE_BYTES_DEFAULT,
  a2aCardUrl,
  a2aFileLimit,
  formatMiB,
} from "@/lib/integrations";
import type {
  ApiTokenCreated,
  Edition,
  Integrations,
  MachineAccount,
  MeDto,
  ProjectPage,
  WorkflowDef,
  WorkflowSummary,
} from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      integrations: vi.fn(),
      createToken: vi.fn(),
      projects: vi.fn(),
      me: vi.fn(),
      edition: vi.fn(),
      workflows: vi.fn(),
      workflowNamed: vi.fn(),
      machineAccounts: vi.fn(),
      createMachineAccount: vi.fn(),
      createMachineToken: vi.fn(),
      myTokens: vi.fn(),
      agents: vi.fn(),
    },
  };
});

vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "varga" }) }));

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

const SUMMARIES: Record<string, WorkflowSummary[]> = {
  "billing-api": [
    { name: "ship-a-change", version: 3, is_default: true, open_items: 0 },
    { name: "hotfix", version: 1, is_default: false, open_items: 0 },
  ],
  contracts: [{ name: "review-contract", version: 1, is_default: true, open_items: 0 }],
};

const definition = (name: string, json: Record<string, unknown>): WorkflowDef =>
  ({ name, version: 1, source: "platform", yaml: "", checksum: "x", is_active: true, json }) as unknown as WorkflowDef;

const DEFINITIONS: Record<string, WorkflowDef> = {
  "billing-api/ship-a-change": definition("ship-a-change", {
    metadata: {
      name: "ship-a-change",
      inputs: {
        type: "object",
        required: ["ticket"],
        properties: { ticket: { type: "string" }, priority: { type: "string", enum: ["low", "high"] } },
      },
    },
    a2a: {
      skill: "ship-a-change",
      description: "Ship a change through review and release.",
      input_modes: ["text/plain", "application/json"],
    },
  }),
  "billing-api/hotfix": definition("hotfix", { metadata: { name: "hotfix", description: "Fix production fast." } }),
  "contracts/review-contract": definition("review-contract", { metadata: { name: "review-contract" } }),
};

const ADMIN = { id: "u1", email: "ada@varga.dev", memberships: [{ org: "varga", role: "org_admin" }] } as unknown as MeDto;
const DEVELOPER = {
  id: "u2",
  email: "dev@varga.dev",
  memberships: [{ org: "varga", role: "developer" }],
} as unknown as MeDto;

const ACCOUNT: MachineAccount = {
  slug: "partner-app",
  display_name: "Partner app",
  address: "partner-app.p1@machines.choregos.invalid",
  writes_per_day: 500,
  created_at: "2026-11-09T10:00:00Z",
  tokens: [],
};

function wrap(children: ReactNode) {
  return (
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <DocsProvider>{children}</DocsProvider>
    </QueryClientProvider>
  );
}

function arrange(me: MeDto = ADMIN) {
  vi.mocked(api.integrations).mockResolvedValue(INFOS);
  vi.mocked(api.projects).mockResolvedValue(PROJECTS);
  vi.mocked(api.me).mockResolvedValue(me);
  vi.mocked(api.edition).mockResolvedValue({
    edition: "community",
    features: [],
    version: "0.25.3",
    docs_url: "https://docs.intranet.example/choregos",
  } as Edition);
  vi.mocked(api.workflows).mockImplementation(async (slug: string) => SUMMARIES[slug] ?? []);
  vi.mocked(api.workflowNamed).mockImplementation(async (slug: string, name: string) => DEFINITIONS[`${slug}/${name}`]!);
  vi.mocked(api.machineAccounts).mockResolvedValue([ACCOUNT]);
}

async function snippet() {
  return (await screen.findByTestId("a2a-snippet")).textContent ?? "";
}

describe("Integrations › An application or another agent (A2A), S62-11", () => {
  beforeEach(() => vi.clearAllMocks());

  it("is an entry of Integrations, at the organisation's scale and in a project", async () => {
    arrange();
    vi.mocked(api.myTokens).mockResolvedValue([]);
    vi.mocked(api.agents).mockResolvedValue([]);
    const { unmount } = render(wrap(<IntegrationsPage client="a2a" />));
    const tab = await screen.findByRole("link", { name: "An application or another agent (A2A)" });
    expect(tab).toHaveAttribute("href", "/integrations/a2a");
    expect(tab).toHaveAttribute("aria-current", "page");
    expect(await screen.findByTestId("a2a-integration")).toBeInTheDocument();
    unmount();
    render(wrap(<IntegrationsPage client="claude-code" projet="billing-api" />));
    expect(await screen.findByRole("link", { name: "An application or another agent (A2A)" })).toHaveAttribute(
      "href",
      "/p/billing-api/integrations/a2a",
    );
  });

  it("shows the card and the endpoint, beside /mcp under the organisation", async () => {
    arrange();
    render(wrap(<A2aIntegration org="varga" />));
    expect(await screen.findByText("https://choregos.example/a2a/orgs/varga/.well-known/agent-card.json")).toBeInTheDocument();
    expect(screen.getByTestId("a2a-endpoint")).toHaveTextContent("https://choregos.example/a2a/orgs/varga");
    expect(a2aCardUrl("https://choregos.example/mcp/", "varga")).toBe(
      "https://choregos.example/a2a/orgs/varga/.well-known/agent-card.json",
    );
  });

  it("step 1: says which workflows are served, their skill, what they take and require", async () => {
    arrange();
    render(wrap(<A2aIntegration org="varga" />));
    const served = await screen.findByTestId("a2a-workflow-ship-a-change");
    expect(within(served).getByText("billing-api/ship-a-change")).toBeInTheDocument();
    expect(served).toHaveTextContent("text/plain, application/json");
    expect(served).toHaveTextContent("ticket");
    expect(served).not.toHaveTextContent("priority");
    // Every project of the organisation is listed, with its own file limit and where to change it.
    expect(await screen.findByTestId("a2a-workflow-review-contract")).toBeInTheDocument();
    expect(screen.getByTestId("a2a-file-limit-contracts")).toHaveTextContent("up to 25 MiB");
    expect(within(screen.getByTestId("a2a-file-limit-contracts")).getByRole("link")).toHaveAttribute(
      "href",
      "/p/contracts/settings/agents#a2a",
    );
  });

  it("step 1: a workflow that is not served gets its block, prefilled, and a link to its YAML tab", async () => {
    arrange();
    render(wrap(<A2aIntegration org="varga" projet="billing-api" />));
    const block = await screen.findByTestId("a2a-block-hotfix");
    expect(block).toHaveTextContent("skill: hotfix");
    expect(block).toHaveTextContent("description: Fix production fast.");
    expect(screen.getByTestId("a2a-yaml-hotfix")).toHaveAttribute("href", "/p/billing-api/workflows/hotfix/yaml");
    // In a project, only that project.
    expect(screen.queryByTestId("a2a-project-contracts")).toBeNull();
  });

  it("step 2: someone who cannot manage members is told why, and still has the personal token", async () => {
    arrange(DEVELOPER);
    render(wrap(<A2aIntegration org="varga" projet="billing-api" />));
    expect(await screen.findByTestId("a2a-cannot-manage")).toHaveTextContent("member:manage");
    expect(screen.queryByTestId("machine-accounts")).toBeNull();
    expect(api.machineAccounts).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Create an A2A token for me" })).toBeInTheDocument();
  });

  it("step 2: an application's token is shown once and lands in the examples, with its address", async () => {
    arrange();
    vi.mocked(api.createMachineToken).mockResolvedValue({
      id: "t1",
      name: "token-1",
      created_at: "2026-11-11T10:00:00Z",
      token: "chg_application_once",
    } as ApiTokenCreated);
    const { unmount } = render(wrap(<A2aIntegration org="varga" projet="billing-api" />));
    expect(await snippet()).toContain("CHOREGOS_A2A_TOKEN=<token>");
    fireEvent.click(await screen.findByRole("button", { name: "Issue a token" }));
    expect(await screen.findByTestId("machine-token-partner-app")).toHaveTextContent("chg_application_once");
    expect(screen.getByTestId("a2a-token-in-snippets")).toHaveTextContent("Partner app");
    await waitFor(async () => expect(await snippet()).toContain("CHOREGOS_A2A_TOKEN=chg_application_once"));
    expect(screen.getByTestId("a2a-follow-up")).toHaveTextContent(
      "machine:partner-app.p1@machines.choregos.invalid for Partner app",
    );
    expect(screen.getByTestId("a2a-follow-up")).toHaveTextContent(
      "choregos machines revoke varga:billing-api partner-app <token id>",
    );
    // Shown once: the page keeps it in memory only. Opened again, it is gone, and the examples say `<token>`.
    unmount();
    render(wrap(<A2aIntegration org="varga" projet="billing-api" />));
    await waitFor(async () => expect(await snippet()).toContain("CHOREGOS_A2A_TOKEN=<token>"));
    expect(document.body.textContent).not.toContain("chg_application_once");
  });

  it("step 2: the personal token is of the a2a scope only, bound to the project, and shown once", async () => {
    arrange();
    vi.mocked(api.createToken).mockResolvedValue({ token: "chg_a2a_once" } as ApiTokenCreated);
    render(wrap(<A2aIntegration org="varga" projet="billing-api" />));
    fireEvent.click(await screen.findByRole("button", { name: "Create an A2A token for me" }));
    expect(await screen.findByTestId("a2a-token")).toHaveTextContent("chg_a2a_once");
    expect(screen.queryByRole("button", { name: "Create an A2A token for me" })).toBeNull();
    const asked = vi.mocked(api.createToken).mock.calls[0]![0];
    expect(asked.scopes).toEqual(["a2a"]);
    expect(asked.project).toBe("varga:billing-api");
    await waitFor(async () => expect(await snippet()).toContain("chg_a2a_once"));
  });

  it("step 3: the examples carry the instance's values, in three tabs", async () => {
    arrange();
    render(wrap(<A2aIntegration org="varga" />));
    await screen.findByTestId("a2a-workflow-ship-a-change");
    await waitFor(async () => expect(await snippet()).toContain("billing-api/ship-a-change"));
    const cli = await snippet();
    expect(cli).toContain("export CHOREGOS_API_URL=https://choregos.example CHOREGOS_ORG=varga");
    expect(cli).toContain("choregos a2a send billing-api/ship-a-change");
    expect(cli).toContain("--field 'ticket=<ticket>'");
    fireEvent.click(screen.getByRole("tab", { name: "curl (A2A 0.3.0)" }));
    const curl = await snippet();
    expect(curl).toContain("export A2A=https://choregos.example/a2a/orgs/varga");
    expect(curl).toContain('"method": "message/send"');
    expect(curl).toContain('"skill": "billing-api/ship-a-change"');
    fireEvent.click(screen.getByRole("tab", { name: "A2A 1.0" }));
    const v1 = await snippet();
    expect(v1).toContain('"method": "SendMessage"');
    expect(v1).toContain("A2A-Version: 1.0");
    expect(screen.getByRole("tab", { name: "A2A 1.0" })).toHaveAttribute("aria-selected", "true");
  });

  it("each step links to its documentation page, on the instance's site", async () => {
    arrange();
    render(wrap(<A2aIntegration org="varga" />));
    await waitFor(() =>
      expect(within(screen.getByTestId("a2a-step-1")).getByTestId("a2a-step-1-docs")).toHaveAttribute(
        "href",
        "https://docs.intranet.example/choregos/integrate/a2a",
      ),
    );
    const step3 = within(screen.getByTestId("a2a-step-3")).getAllByTestId("a2a-step-3-docs");
    expect(step3.map((link) => link.getAttribute("href"))).toEqual([
      "https://docs.intranet.example/choregos/integrate/a2a",
      "https://docs.intranet.example/choregos/cli/install",
    ]);
    for (const n of [2, 4]) {
      expect(within(screen.getByTestId(`a2a-step-${n}`)).getAllByTestId(`a2a-step-${n}-docs`)[0]).toHaveAttribute(
        "href",
        "https://docs.intranet.example/choregos/integrate/a2a",
      );
    }
    expect(screen.getByTestId("a2a-guide")).toHaveAttribute("href", "https://docs.intranet.example/choregos/integrate/a2a");
  });

  it("a project with no served workflow says the examples use a placeholder", async () => {
    arrange();
    render(wrap(<A2aIntegration org="varga" projet="contracts" />));
    await waitFor(() => expect(screen.getByTestId("a2a-no-skill")).toHaveTextContent("varga:contracts"));
    expect(await snippet()).toContain("choregos a2a send <project>/<skill>");
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
