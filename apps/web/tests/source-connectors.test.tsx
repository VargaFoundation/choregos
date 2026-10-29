import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { AgentMcpServers } from "@/components/agents/definition-view";
import { KnowledgeSources } from "@/components/knowledge-sources";
import { OrganisationMemoryStore, ProjectMemoryStore } from "@/components/memory-store";
import { api } from "@/lib/api";
import { orgConnectorHref, projectConnectionsHref } from "@/lib/connector-link";
import type { ConnectorDto, KnowledgeSource, OrgConnector } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, orgConnectors: vi.fn(), connectors: vi.fn() } };
});

const wrap = (node: ReactNode) =>
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{node}</QueryClientProvider>);

const org = (name: string, kind: string, type: string) => ({ name, kind, type, operations: [] }) as unknown as OrgConnector;
const project = (kind: string, type: string) => ({ kind, type, config: {} }) as unknown as ConnectorDto;
const source = (id: string, kind: string, connector: string | null): KnowledgeSource =>
  ({ id, kind, name: `source ${id}`, connector, config: {}, status: "idle", document_count: 0 }) as unknown as KnowledgeSource;

describe("where a connector is read", () => {
  it("an organisation connector is a card of Installed connectors, a project's is in its Connections", () => {
    expect(orgConnectorHref("sp docs")).toBe("/admin/connectors?connector=sp%20docs");
    expect(projectConnectionsHref("billing-api")).toBe("/p/billing-api/settings#connections");
  });
});

describe("a knowledge source says which connector it syncs through", () => {
  it("links the connector, with its type", async () => {
    vi.mocked(api.orgConnectors).mockResolvedValue([org("sp-docs", "documents", "sharepoint")]);
    wrap(<KnowledgeSources org="varga" slug="base" sources={[source("a", "sharepoint", "sp-docs")]} onChange={vi.fn()} canEdit={false} />);
    const line = await screen.findByText(/\(sharepoint\)/);
    const tag = line.closest("[data-testid='source-connector-a']") as HTMLElement;
    expect(tag).toHaveTextContent("via connector sp-docs (sharepoint)");
    expect(within(tag).getByRole("link", { name: "sp-docs" })).toHaveAttribute("href", "/admin/connectors?connector=sp-docs");
  });

  it("a member who cannot read the organisation's connectors still sees its name, without a link", async () => {
    vi.mocked(api.orgConnectors).mockRejectedValue(new Error("403"));
    wrap(<KnowledgeSources org="varga" slug="base" sources={[source("a", "confluence", "wiki")]} onChange={vi.fn()} canEdit={false} />);
    const tag = await screen.findByTestId("source-connector-a");
    await vi.waitFor(() => expect(within(tag).queryByRole("link")).toBeNull());
    expect(tag).toHaveTextContent("via connector wiki");
  });

  it("a connector no longer declared says so, and a web source says it has none", async () => {
    vi.mocked(api.orgConnectors).mockResolvedValue([]);
    wrap(
      <KnowledgeSources
        org="varga"
        slug="base"
        sources={[source("a", "confluence", "gone"), source("b", "web", null)]}
        onChange={vi.fn()}
        canEdit={false}
      />,
    );
    expect(await screen.findByText(/no longer declared/)).toBeInTheDocument();
    expect(screen.getByTestId("source-connector-b")).toHaveTextContent("no connector: the public web");
  });
});

describe("a memory says which store holds it", () => {
  it("a project's: its memory connector, else the deployment's default", async () => {
    vi.mocked(api.connectors).mockResolvedValue([project("tracker", "jira"), project("memory", "lexical")]);
    const { unmount } = wrap(<ProjectMemoryStore slug="billing-api" />);
    const store = await screen.findByTestId("memory-store");
    expect(store).toHaveTextContent("Stored in the project's memory connector (lexical).");
    expect(within(store).getByRole("link")).toHaveAttribute("href", "/p/billing-api/settings#connections");
    unmount();

    vi.mocked(api.connectors).mockResolvedValue([project("tracker", "jira")]);
    wrap(<ProjectMemoryStore slug="other" />);
    expect(await screen.findByTestId("memory-store")).toHaveTextContent("the deployment's default memory store");
  });

  it("the organisation's: its first memory connector by name, as the server picks it", async () => {
    vi.mocked(api.orgConnectors).mockResolvedValue([
      org("zeta-memory", "memory", "ecphoria"),
      org("alpha-memory", "memory", "lexical"),
      org("sp-docs", "documents", "sharepoint"),
    ]);
    wrap(<OrganisationMemoryStore org="varga" />);
    const store = await screen.findByTestId("memory-store");
    expect(store).toHaveTextContent("Stored in the organisation's memory connector alpha-memory (lexical).");
    expect(within(store).getByRole("link", { name: "alpha-memory" })).toHaveAttribute(
      "href",
      "/admin/connectors?connector=alpha-memory",
    );
  });
});

describe("an agent says which MCP connectors it may call", () => {
  it("each one links to its card", () => {
    render(<AgentMcpServers servers={[{ connector: "rovo", tools: ["search"] }, { connector: "sentry", tools: [] }]} />);
    expect(screen.getByRole("link", { name: "rovo" })).toHaveAttribute("href", "/admin/connectors?connector=rovo");
    expect(screen.getByRole("link", { name: "sentry" }).parentElement).toHaveTextContent("sentry — every tool");
  });
});
