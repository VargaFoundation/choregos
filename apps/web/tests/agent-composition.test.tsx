import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AgentComposition } from "@/components/agents/composition";
import { providerMark } from "@/components/provider-logo";
import { missingComponents } from "@/components/agents/spec-summary";
import { harnessOf } from "@/lib/harness";
import { groupBySize, resolveModel } from "@/lib/model-resolution";
import type { Agent, AgentSpec, KnowledgeBase, ModelCatalogue, Skill } from "@/lib/types";

/**
 * The Overview tab's Composition card (S60-07, ADR 0127, redone 2026-11-12): what the model resolves to for
 * the organisation, the tools by origin, knowledge, skills and memory as links, the limits.
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

const entry = (model_name: string, gateway: NonNullable<ModelCatalogue["entries"][number]["gateway"]>, display_name = model_name) => ({
  id: model_name, model_name, display_name, source: "gateway" as const, enabled: true, allowed_roles: [], usages: ["agent" as const], gateway,
});

const CATALOGUE: ModelCatalogue = {
  mode: "open",
  gateway: { type: "litellm" },
  tiers: { cheap: null, standard: null, strong: "platform/deep", aux: null, embed: null, local: null },
  entries: [
    entry("platform/standard", { provider: "openai", upstream_model: "openai/gpt-5-mini" }, "GPT-5 mini"),
    entry("platform/deep", { provider: "anthropic", upstream_model: "anthropic/claude-opus-5", supports_reasoning: true }, "Claude Opus 5"),
  ],
};

const BASES = [{ slug: "adr-framework", name: "ADR framework", document_count: 12, serving_mode: "index" }] as unknown as KnowledgeBase[];
const SKILLS = [{ slug: "review", description: "Reviews a diff against the team's checklist.", status: "active", latest_version: 1 }] as unknown as Skill[];

function show(agent: Agent, loaded: { catalogue?: ModelCatalogue; bases?: KnowledgeBase[]; skills?: Skill[] } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  if (loaded.catalogue) client.setQueryData(["model-catalogue", "acme"], loaded.catalogue);
  if (loaded.bases) client.setQueryData(["knowledge", "acme"], loaded.bases);
  if (loaded.skills) client.setQueryData(["skills", "acme"], loaded.skills);
  return render(
    <QueryClientProvider client={client}>
      <AgentComposition org="acme" agent={agent} />
    </QueryClientProvider>,
  );
}

const card = () => screen.getByTestId("agent-composition");

describe("AgentComposition", () => {
  it("says a complete agent section by section, with links and the Definition and Access tabs", () => {
    show(agentOf(COMPLETE), { catalogue: CATALOGUE, bases: BASES, skills: SKILLS });
    expect(within(card()).getByTestId("composition-complete")).toHaveTextContent("complete");
    expect(within(card()).queryByTestId("composition-inherits")).toBeNull();
    expect(within(card()).getByTestId("composition-model")).toHaveTextContent("profile:standard");
    expect(within(card()).getByTestId("composition-tools-catalogue")).toHaveTextContent("read_file");
    expect(within(card()).getByTestId("composition-tools-catalogue")).toHaveTextContent("group:git");
    expect(within(card()).getByTestId("composition-tools-web")).toHaveTextContent("web fetch");
    expect(within(card()).getByTestId("composition-tools-extensions")).toHaveTextContent("none declared");
    expect(within(card()).getByTestId("composition-definition-link")).toHaveAttribute("href", "/agents/developer?tab=definition");
    expect(within(card()).getByTestId("composition-access-link")).toHaveAttribute("href", "/agents/developer?tab=access");
  });

  it("links every MCP server, extension and catalogue tool to a page", () => {
    show(agentOf({ ...COMPLETE, tools: { catalogue: ["read_file"], extensions: ["jira-tools"], web: [] } }));
    expect(screen.getByTestId("composition-mcp-jira")).toHaveAttribute("href", "/admin/connectors?connector=jira");
    expect(screen.getByTestId("composition-tool-extension-jira-tools")).toHaveAttribute("href", "/library/plugins");
    expect(screen.getByTestId("composition-tool-catalogue-read_file").getAttribute("href")).toMatch(/\/build\/tools$/);
  });

  it("says what an agent leaves to its project, instead of showing nothing", () => {
    show(agentOf({ instructions: "Code.", knowledge: [], tools: { catalogue: ["read_file"] } } as AgentSpec));
    expect(within(card()).getByTestId("composition-inherits")).toHaveTextContent("inherits: model, extensions, web tools, memory");
    expect(within(card()).queryByTestId("composition-complete")).toBeNull();
    expect(within(card()).getByTestId("composition-model")).toHaveTextContent("Inherited from the project");
    expect(within(card()).getByTestId("composition-tools-extensions")).toHaveTextContent("Inherited from the project");
    expect(within(card()).getByTestId("composition-memory")).toHaveTextContent("Inherited from the project");
  });

  it("renders the by_size resolution: each size, its model, provider logo and reasoning flag", () => {
    show(agentOf({ ...COMPLETE, model: "profile:by_size" }), { catalogue: CATALOGUE });
    const standard = screen.getByTestId("composition-model-platform/standard");
    expect(standard).toHaveTextContent("Size S, M");
    expect(standard).toHaveTextContent("GPT-5 mini");
    expect(standard).toHaveTextContent("openai/gpt-5-mini");
    expect(within(standard).getByRole("img", { name: "OpenAI" })).toHaveTextContent("O");
    expect(standard).not.toHaveTextContent("reasoning"); // the gateway never said: nothing is claimed
    const strong = screen.getByTestId("composition-model-platform/deep");
    expect(strong).toHaveTextContent("Size L, XL");
    expect(strong).toHaveTextContent("Claude Opus 5");
    expect(strong).toHaveTextContent("XL: turn limit × 1.5");
    expect(strong).toHaveTextContent("reasoning model");
    expect(within(strong).getByRole("img", { name: "Anthropic" })).toBeInTheDocument();
  });

  it("says nothing it does not know about the model, and says the resolution is missing when the catalogue did not load", () => {
    show(agentOf({ ...COMPLETE, model: "profile:by_size" }));
    expect(screen.getByTestId("composition-model")).toHaveTextContent("is not loaded");
    expect(screen.queryByRole("img", { name: "OpenAI" })).toBeNull();
  });

  it("names the knowledge base, with its documents and serving mode, the skill with its description, the memory with scope and mode", () => {
    show(agentOf({ ...COMPLETE, memories: [{ scope: "global", slug: "lessons", mode: "read" }, { scope: "project", slug: "shared", mode: "write" }] }), {
      catalogue: CATALOGUE, bases: BASES, skills: SKILLS,
    });
    const kb = screen.getByTestId("composition-kb-adr-framework");
    expect(within(kb).getByRole("link", { name: "ADR framework" })).toHaveAttribute("href", "/knowledge/adr-framework");
    expect(kb).toHaveTextContent("12 documents");
    expect(kb).toHaveTextContent("searched by index");
    const skill = screen.getByTestId("composition-skill-review");
    expect(within(skill).getByRole("link", { name: "review" })).toHaveAttribute("href", "/skills/review");
    expect(skill).toHaveTextContent("Reviews a diff against the team's checklist.");
    expect(within(screen.getByTestId("composition-memory-global-lessons")).getByRole("link")).toHaveAttribute("href", "/memories/lessons");
    expect(screen.getByTestId("composition-memory-global-lessons")).toHaveTextContent("organisation · reads");
    expect(screen.getByTestId("composition-memory-project-shared")).toHaveTextContent("project · reads and writes");
  });

  it("links a knowledge base and a skill by slug while their lists are not loaded", () => {
    show(agentOf(COMPLETE));
    expect(within(screen.getByTestId("composition-kb-adr-framework")).getByRole("link", { name: "adr-framework" })).toHaveAttribute("href", "/knowledge/adr-framework");
  });

  it("says plainly there is nothing attached, never that the agent is empty", () => {
    show(agentOf({ ...COMPLETE, knowledge: [], skills: [], mcp_servers: [], tools: { catalogue: [], extensions: [], web: [] } }));
    expect(screen.getByTestId("composition-knowledge")).toHaveTextContent("No knowledge base attached.");
    expect(screen.getByTestId("composition-skills")).toHaveTextContent("No skill attached.");
    expect(screen.getByTestId("composition-tools-mcp")).toHaveTextContent("none declared");
    expect(screen.getByTestId("composition-tools-catalogue")).toHaveTextContent("none declared");
    expect(card()).not.toHaveTextContent("(closed)");
    // the legend is said once, not on every row
    expect(screen.getAllByText(/none declared” means/)).toHaveLength(1);
  });

  it("shows what a coding agent's harness brings when the version names no tool", () => {
    show(agentOf({ ...COMPLETE, backend: null, tools: { catalogue: [], extensions: [], web: [] } }));
    const builtin = screen.getByTestId("composition-builtin-tools");
    for (const id of ["files", "shell", "git", "search", "web"]) expect(within(builtin).getByTestId(`composition-builtin-${id}`)).toBeInTheDocument();
    expect(screen.getByTestId("composition-harness")).toHaveTextContent("project's coding agent");
  });

  it("says a prompt agent has no built-in tool", () => {
    show(agentOf({ ...COMPLETE, runtime: { kind: "prompt" } } as AgentSpec));
    expect(screen.getByTestId("composition-builtin-none")).toHaveTextContent("prompt agent");
    expect(screen.queryByTestId("composition-builtin-tools")).toBeNull();
  });

  it("shows turns, minutes and the budget of a run", () => {
    show(agentOf({ ...COMPLETE, limits: { max_turns: 120, max_minutes: 90 }, budget: { run_usd: 12, daily_usd: 40 } }));
    const limits = screen.getByTestId("composition-limits");
    expect(limits).toHaveTextContent("120 a run");
    expect(limits).toHaveTextContent("90 min a run");
    expect(limits).toHaveTextContent("$12.00 a run");
    expect(limits).toHaveTextContent("$40.00 a day");
  });

  it("says the role's defaults apply when the version sets no limit", () => {
    show(agentOf(COMPLETE));
    expect(screen.getByTestId("composition-limits")).toHaveTextContent("role's defaults");
  });
});

describe("resolveModel", () => {
  it("follows choregos_core.models: S and M standard, L and XL strong with 1.5 times the turns, the org's tier first", () => {
    const rows = resolveModel("profile:by_size", CATALOGUE);
    expect(rows.map((r) => [r.size, r.profile, r.modelName, r.turnsFactor])).toEqual([
      ["S", "standard", "platform/standard", 1],
      ["M", "standard", "platform/standard", 1],
      ["L", "strong", "platform/deep", 1],
      ["XL", "strong", "platform/deep", 1.5],
    ]);
    expect(groupBySize(rows).map((g) => g.sizes)).toEqual([["S", "M"], ["L", "XL"]]);
  });

  it("resolves a profile, a direct model, and nothing for an empty request; an unlisted model carries no fact", () => {
    expect(resolveModel("profile:cheap", undefined)[0]).toMatchObject({ modelName: "platform/cheap", provider: null, reasoning: null, known: false });
    expect(resolveModel("openai/gpt-5", CATALOGUE)[0]).toMatchObject({ modelName: "openai/gpt-5", profile: null, known: false });
    expect(resolveModel("", CATALOGUE)).toEqual([]);
  });
});

describe("providerMark", () => {
  it("gives the usual providers a monogram, keeps an unknown one's own name, and says nothing without a provider", () => {
    expect(providerMark("openai")).toEqual({ label: "OpenAI", monogram: "O" });
    expect(providerMark("azure")?.label).toBe("Azure OpenAI");
    expect(providerMark("vertex_ai")?.label).toBe("Google");
    expect(providerMark("ollama")?.label).toBe("Ollama");
    expect(providerMark("mistral")?.label).toBe("Mistral AI");
    expect(providerMark("cohere")).toEqual({ label: "cohere", monogram: "C" });
    expect(providerMark("")).toBeNull();
    expect(providerMark(null)).toBeNull();
  });
});

describe("harnessOf", () => {
  it("gives a coding agent files, shell, git, search, and the CLI's web tools unless the network policy removes them", () => {
    expect(harnessOf({}).tools.map((t) => t.id)).toEqual(["files", "shell", "git", "search", "web"]);
    expect(harnessOf({ network: { builtin_web_tools: false } }).tools.map((t) => t.id)).not.toContain("web");
    expect(harnessOf({ backend: "codex" }).tools.map((t) => t.id)).not.toContain("web");
  });
  it("takes no git nor search when no repository is cloned, and no tool for a runtime without a sandbox", () => {
    expect(harnessOf({ workspace: "none" }).tools.map((t) => t.id)).toEqual(["files", "shell", "web"]);
    expect(harnessOf({ runtime: { kind: "a2a" } } as AgentSpec)).toMatchObject({ kind: "none", tools: [] });
  });
});

describe("missingComponents", () => {
  it("follows ADR 0127 section 7: a2a complete, voice needs its model only", () => {
    expect(missingComponents({ runtime: { kind: "a2a" } } as unknown as AgentSpec)).toEqual([]);
    expect(missingComponents({ runtime: { kind: "voice" } } as unknown as AgentSpec)).toEqual(["model"]);
    expect(missingComponents({ instructions: "x" } as AgentSpec)).toEqual(["model", "tools.catalogue", "tools.extensions", "tools.web", "memories"]);
  });
});
