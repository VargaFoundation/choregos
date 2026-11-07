import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { groupOfAnchor } from "@/components/project-settings/groups";
import { ToolsSection } from "@/components/project-settings/tools-section";
import { api } from "@/lib/api";
import type { PolicyCeilingState, PolicyDef } from "@/lib/types";

/**
 * The project's Agents settings show whether its agents may call their tools from a script (S57-10,
 * ADR 0122 §6): the policy's `tools` section, and the organisation's ceiling when it forbids them.
 * Read-only: the policy is edited as YAML in the guardrails.
 */
const session = vi.hoisted(() => ({ orgs: [] as { slug: string; name: string; tool_scripts?: "enabled" | "disabled" | null }[] }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme", me: null, orgs: session.orgs }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, projectTools: vi.fn(), policy: vi.fn(), policyCeiling: vi.fn() } };
});

type Tools = NonNullable<PolicyDef["json"]>["tools"];
const policy = (tools: Tools, clamps: string[] = []): PolicyDef => ({
  name: "default",
  version: 3,
  yaml: "",
  json: { apiVersion: "choregos/v1", kind: "Policy", metadata: { name: "default", version: 3 }, ...(tools ? { tools } : {}) },
  is_active: true,
  clamps,
});
const ceiling = (scripts: "off" | "offer" | null): PolicyCeilingState => ({ ceiling: { tools: { scripts } }, updated_by: null, updated_at: null });

async function show(def: PolicyDef, state: PolicyCeilingState = { ceiling: null }) {
  vi.mocked(api.projectTools).mockResolvedValue({ allows_all: false, tools: [] } as never);
  vi.mocked(api.policy).mockResolvedValue(def);
  vi.mocked(api.policyCeiling).mockResolvedValue(state);
  await act(async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <ToolsSection slug="dev" />
      </QueryClientProvider>,
    );
  });
  return screen.findByTestId("tool-scripts");
}

afterEach(() => {
  vi.clearAllMocks();
  session.orgs = [];
});

describe("tool scripts in the project's Agents settings", () => {
  it("lives in the Agents group, beside the tool catalogue", () => {
    expect(groupOfAnchor("tools")?.id).toBe("agents");
  });

  it("an absent `tools` section reads off, four calls at once by default", async () => {
    const card = await show(policy(undefined));
    expect(within(card).getByTestId("tool-scripts-value")).toHaveTextContent("off");
    expect(within(card).getByTestId("tool-scripts-concurrency")).toHaveTextContent("4 (the default)");
    expect(within(card).queryByText("The organisation forbids tool scripts")).toBeNull();
    expect(within(card).getByTestId("tool-scripts-agent-default")).toHaveTextContent("an agent whose catalogue entry offers them");
  });

  it("a policy that says `off` leaves no agent's default (S57-15)", async () => {
    const card = await show(policy({ scripts: "off" }));
    expect(within(card).getByTestId("tool-scripts-value")).toHaveTextContent("off");
    expect(within(card).queryByTestId("tool-scripts-agent-default")).toBeNull();
  });

  it("shows what the policy offers, per role, and where to change it", async () => {
    const card = await show(
      policy({ scripts: "offer", max_concurrent_calls: 6, per_role: { review: { scripts: "off" } } }),
      ceiling(null),
    );
    expect(within(card).getByTestId("tool-scripts-value")).toHaveTextContent("offer");
    expect(within(card).getByTestId("tool-scripts-concurrency")).toHaveTextContent("6");
    expect(within(card).getByTestId("tool-scripts-concurrency")).not.toHaveTextContent("the default");
    expect(within(card).getByTestId("tool-scripts-roles")).toHaveTextContent("review: off");
    expect(within(card).getByRole("link", { name: "edit the policy" })).toHaveAttribute("href", "/p/dev/settings/security#guardrails");
    expect(within(card).queryByText("The organisation forbids tool scripts")).toBeNull();
  });

  it("says the organisation's ceiling forbids them, shows what runs get, and offers no edit", async () => {
    const card = await show(
      policy({ scripts: "offer" }, ["tools.scripts: `offer` is refused, the organisation forbids tool scripts"]),
      ceiling("off"),
    );
    const refusal = within(card).getByRole("status");
    expect(refusal).toHaveTextContent("The organisation forbids tool scripts");
    expect(refusal).toHaveTextContent("a policy that offers them is refused when it is saved");
    expect(within(card).getByTestId("tool-scripts-value")).toHaveTextContent("offer");
    expect(within(card).getByTestId("tool-scripts-value")).toHaveTextContent("runs get off");
    expect(within(card).getByTestId("tool-scripts-clamps")).toHaveTextContent("tools.scripts: `offer` is refused");
    expect(within(card).queryByRole("link", { name: "edit the policy" })).toBeNull();
    expect(within(card).getByTestId("tool-scripts-edit")).toHaveTextContent("Only an organisation administrator can lift the ban");
  });

  describe("the organisation's switch (S57-05b)", () => {
    const withChoice = (tool_scripts: "enabled" | "disabled" | null) => {
      session.orgs = [{ slug: "acme", name: "Acme", tool_scripts }];
    };

    it("says when the organisation has them disabled, and that runs get none", async () => {
      withChoice("disabled");
      const card = await show(policy({ scripts: "offer" }), ceiling(null));
      expect(within(card).getByRole("status")).toHaveTextContent("The organisation has tool scripts disabled");
      expect(within(card).getByRole("status")).toHaveTextContent("No run of this project is offered them");
      expect(within(card).getByTestId("tool-scripts-org")).toHaveTextContent("disabled");
      expect(within(card).getByTestId("tool-scripts-value")).toHaveTextContent("runs get off");
      expect(within(card).getByTestId("tool-scripts-edit")).toHaveTextContent("Only an organisation administrator can switch them on");
      expect(within(card).queryByRole("link", { name: "edit the policy" })).toBeNull();
    });

    it("an organisation that enables them leaves the policy to decide", async () => {
      withChoice("enabled");
      const card = await show(policy({ scripts: "offer" }), ceiling(null));
      expect(within(card).queryByText("The organisation has tool scripts disabled")).toBeNull();
      expect(within(card).getByTestId("tool-scripts-org")).toHaveTextContent("enabled");
      expect(within(card).getByTestId("tool-scripts-value")).not.toHaveTextContent("runs get off");
      expect(within(card).getByRole("link", { name: "edit the policy" })).toBeTruthy();
    });

    it("an organisation that does not choose follows the deployment's default, which the page does not see", async () => {
      withChoice(null);
      const card = await show(policy({ scripts: "offer" }), ceiling(null));
      expect(within(card).queryByText("The organisation has tool scripts disabled")).toBeNull();
      expect(within(card).getByTestId("tool-scripts-org")).toHaveTextContent("the deployment's default");
      expect(within(card).getByTestId("tool-scripts-org")).toHaveTextContent("which this page does not see");
    });
  });
});
