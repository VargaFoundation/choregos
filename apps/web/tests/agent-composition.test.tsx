import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AgentComposition } from "@/components/agents/composition";
import { missingComponents } from "@/components/agents/spec-summary";
import type { Agent, AgentAccess, AgentSpec } from "@/lib/types";

/**
 * The Overview tab's Composition card (S60-07, ADR 0127): the five components of the current version, a
 * badge that says complete or what it inherits, and links to the Definition and Access tabs.
 */
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), usePathname: () => "/agents/developer" }));

const agentOf = (spec: AgentSpec): Agent => ({
  slug: "developer",
  kind: "internal",
  display_name: "Developer",
  status: "active",
  lifecycle: "active",
  latest_version: 2,
  scope: "org",
  versions: [
    { version: 1, spec: { instructions: "Old." }, checksum: "sha256:1", pinned_by: [] },
    { version: 2, spec, checksum: "sha256:2", pinned_by: [] },
  ],
});

const COMPLETE: AgentSpec = {
  instructions: "Code.",
  model: "profile:standard",
  tools: { catalogue: ["read_file", "group:git"], extensions: [], web: ["fetch"] },
  mcp_servers: [{ connector: "jira" }],
  knowledge: [{ slug: "adr-framework" }],
  skills: [{ slug: "review" }],
  memories: [{ scope: "project", slug: "shared", mode: "write" }],
} as AgentSpec;

function show(agent: Agent, access?: AgentAccess) {
  const client = new QueryClient();
  if (access) client.setQueryData(["agent-access", "acme", "developer", access.project, null], access);
  return render(
    <QueryClientProvider client={client}>
      <AgentComposition org="acme" agent={agent} />
    </QueryClientProvider>,
  );
}

describe("AgentComposition", () => {
  it("says a complete agent: model, tools by source, knowledge, skills, memory, and that it is complete", () => {
    show(agentOf(COMPLETE));
    const card = screen.getByTestId("agent-composition");
    expect(within(card).getByTestId("composition-complete")).toHaveTextContent("complete");
    expect(within(card).queryByTestId("composition-inherits")).toBeNull();
    expect(within(card).getByTestId("composition-model")).toHaveTextContent("profile:standard");
    expect(within(card).getByTestId("composition-tools-catalogue")).toHaveTextContent("2 named");
    expect(within(card).getByTestId("composition-tools-extensions")).toHaveTextContent("none (closed)");
    expect(within(card).getByTestId("composition-tools-web")).toHaveTextContent("1 named");
    expect(within(card).getByTestId("composition-tools-mcp")).toHaveTextContent("1 (jira)");
    expect(within(card).getByTestId("composition-knowledge")).toHaveTextContent("adr-framework");
    expect(within(card).getByTestId("composition-skills")).toHaveTextContent("review");
    expect(within(card).getByTestId("composition-memory")).toHaveTextContent("shared");
    expect(within(card).getByTestId("composition-definition-link")).toHaveAttribute("href", "/agents/developer?tab=definition");
    expect(within(card).getByTestId("composition-access-link")).toHaveAttribute("href", "/agents/developer?tab=access");
  });

  it("says what an agent leaves to its project, instead of showing nothing", () => {
    show(agentOf({ instructions: "Code.", knowledge: [], tools: { catalogue: ["read_file"] } } as AgentSpec));
    const card = screen.getByTestId("agent-composition");
    expect(within(card).getByTestId("composition-inherits")).toHaveTextContent("inherits: model, extensions, web tools, memory");
    expect(within(card).queryByTestId("composition-complete")).toBeNull();
    expect(within(card).getByTestId("composition-model")).toHaveTextContent("Inherited from the project");
    expect(within(card).getByTestId("composition-tools-extensions")).toHaveTextContent("Inherited from the project");
    expect(within(card).getByTestId("composition-tools-web")).toHaveTextContent("Inherited from the project");
    expect(within(card).getByTestId("composition-memory")).toHaveTextContent("Inherited from the project");
  });

  it("says plainly that there is no knowledge base", () => {
    show(agentOf({ ...COMPLETE, knowledge: [], skills: [] }));
    expect(screen.getByTestId("composition-knowledge")).toHaveTextContent("No knowledge base");
    expect(screen.getByTestId("composition-skills")).toHaveTextContent("No skill");
  });

  it("adds the resolved model when an Access answer for this version is already known, and only then", () => {
    const access = {
      agent: "developer", version: 2, project: "billing-api", complete: true, inherits: [],
      model: { requested: "profile:standard", resolved: "platform/standard-1", origin: "agent" },
      tools: [], knowledge: [], memories: [],
    } as AgentAccess;
    const { unmount } = show(agentOf(COMPLETE), access);
    expect(screen.getByTestId("composition-model")).toHaveTextContent("resolves to platform/standard-1 in billing-api");
    unmount();
    show(agentOf(COMPLETE));
    expect(screen.getByTestId("composition-model")).not.toHaveTextContent("resolves to");
  });
});

describe("missingComponents", () => {
  it("follows ADR 0127 section 7: a2a complete, voice needs its model only", () => {
    expect(missingComponents({ runtime: { kind: "a2a" } } as unknown as AgentSpec)).toEqual([]);
    expect(missingComponents({ runtime: { kind: "voice" } } as unknown as AgentSpec)).toEqual(["model"]);
    expect(missingComponents({ instructions: "x" } as AgentSpec)).toEqual(["model", "tools.catalogue", "tools.extensions", "tools.web", "memories"]);
  });
});
