import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { BuiltinAgentDetails, BuiltinAgentOfActor, groupBySize } from "@/components/agents/builtin-agent";
import { api } from "@/lib/api";
import { builtinAgentDemo } from "@/mocks/data";

/**
 * A workflow actor that names no registry agent used to read "built-in playbook, not a registry
 * agent" on the map and nothing else: which model, which prompt, which tools were anyone's guess.
 * The card now says what a run loads, from `GET /projects/{id}/builtin-agent`.
 */
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, describeBuiltinAgent: vi.fn() } };
});

const byDefault = builtinAgentDemo("/projects/p/builtin-agent?role=implement");

describe("the built-in agent card", () => {
  it("says it is a built-in agent, the model of each size, the backend, the limits and the tools", () => {
    render(<BuiltinAgentDetails agent={byDefault} projectSlug="acme" />);
    expect(screen.getByText("built-in agent")).toBeInTheDocument();
    const models = within(screen.getByTestId("builtin-agent-models")).getAllByRole("listitem");
    expect(models.map((li) => li.textContent)).toEqual([
      "size S, M → standard: anthropic/claude-sonnet-5-5 (alias platform/standard)",
      "size L → strong: anthropic/claude-opus-5-5 (alias platform/strong)",
      "size XL → strong: anthropic/claude-opus-5-5 (alias platform/strong) · turns ×1.5",
    ]);
    expect(screen.getByText("claude-code")).toBeInTheDocument();
    expect(screen.getByText(/the project's default backend/)).toBeInTheDocument();
    expect(screen.getByText(/80 turns · 60 min/)).toBeInTheDocument();
    const tools = screen.getByTestId("builtin-agent-tools");
    expect(tools).toHaveTextContent("MCP choregos");
    expect(within(tools).getByText("report_finding")).toBeInTheDocument();
    expect(screen.getByTestId("builtin-agent-prompt")).toHaveTextContent("<work item title>");
    expect(screen.getByRole("link", { name: "Register it" })).toHaveAttribute("href", "/p/acme/agents");
  });

  it("says why a size would be refused rather than hiding it", () => {
    const refused = {
      ...byDefault,
      models: [{ size: "L", error: "the model `x` is not enabled", turns_factor: 1, warnings: [] }],
    };
    render(<BuiltinAgentDetails agent={refused} />);
    expect(screen.getByText("refused: the model `x` is not enabled")).toBeInTheDocument();
  });

  it("says an unknown playbook instead of an empty prompt", () => {
    render(<BuiltinAgentDetails agent={{ ...byDefault, instructions: null, instructions_error: "unknown playbook: unicorn" }} />);
    expect(screen.getByText("unknown playbook: unicorn")).toBeInTheDocument();
    expect(screen.queryByTestId("builtin-agent-prompt")).toBeNull();
  });

  it("groups only the sizes that resolve to the same model, in order", () => {
    expect(groupBySize(byDefault.models ?? []).map((g) => g.sizes)).toEqual([["S", "M"], ["L"], ["XL"]]);
  });

  it("asks the API with the actor's own fields, a draft's included", async () => {
    vi.mocked(api.describeBuiltinAgent).mockResolvedValue(byDefault);
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <BuiltinAgentOfActor projectSlug="acme" actor={{ role: "verify", model: "profile:strong", max_turns: 12 }} />
      </QueryClientProvider>,
    );
    await screen.findByTestId("builtin-agent");
    await waitFor(() =>
      expect(api.describeBuiltinAgent).toHaveBeenCalledWith("acme", {
        role: "verify",
        playbook: undefined,
        model: "profile:strong",
        backend: undefined,
        max_turns: 12,
        max_minutes: undefined,
      }),
    );
  });
});
