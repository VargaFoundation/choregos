import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { McpClients, READ_ONLY_TOOLS, versionDuClient } from "@/components/agents/mcp-clients";
import {
  implicitFromApi,
  isMcpClient,
  clientState,
  versionSummary,
  actorSlug,
  actorVersion,
} from "@/components/agents/registry";
import { api } from "@/lib/api";
import type { ApiToken } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
    api: {
      ...reel.api,
      createAgent: vi.fn(),
      attachAgentToken: vi.fn(),
      agentCredentials: vi.fn(),
      orgAgentCredentials: vi.fn().mockResolvedValue([]),
    },
  };
});

describe("a project's implicit agents (S18-07, computed by the API since S30-04)", () => {
  const fromApi = [
    { workflow: "onboarding", actor: "redacteur", role: "refine", model: "profile:standard", backend: null, max_turns: 30, max_minutes: null },
  ];

  it("the API's list takes the console's shape", () => {
    expect(implicitFromApi(fromApi)).toEqual([
      { workflow: "onboarding", acteur: "redacteur", role: "refine", model: "profile:standard", backend: undefined, max_turns: 30, max_minutes: undefined },
    ]);
  });

  it("registered, it keeps what the workflow said of it, and nothing more", () => {
    const redacteur = implicitFromApi(fromApi).find((i) => i.acteur === "redacteur");
    expect(actorVersion(redacteur!)).toEqual({ model: "profile:standard", limits: { max_turns: 30, max_minutes: null } });
    expect(actorSlug("Rédacteur RH_2")).toBe("r-dacteur-rh-2"); // lang: fr-ok (a non-ASCII letter becomes a dash)
  });
});

describe("les clients MCP", () => {
  it("a token that opens the MCP door is a client; a broad token is not", () => {
    expect(isMcpClient({ scopes: ["mcp:read"] })).toBe(true);
    expect(isMcpClient({ scopes: ["*"] })).toBe(false);
  });

  it("connected, only if it called, and no longer once revoked", () => {
    const maintenant = new Date("2026-10-05T12:00:00Z");
    expect(clientState({ last_used_at: null }, maintenant)).toEqual({ connecte: false, recent: false });
    expect(clientState({ last_used_at: "2026-10-05T11:57:00Z" }, maintenant)).toEqual({ connecte: true, recent: true });
    expect(clientState({ last_used_at: "2026-10-01T11:57:00Z" }, maintenant)).toEqual({ connecte: true, recent: false });
    expect(clientState({ last_used_at: "2026-10-05T11:57:00Z", revoked_at: "2026-10-05T11:58:00Z" }, maintenant).connecte).toBe(
      false,
    );
  });

  it("read-only, an external agent names only tools that read", () => {
    expect(versionDuClient(false)).toEqual({});
    expect(versionDuClient(true)).toEqual({ mcp_servers: [{ connector: "choregos", tools: READ_ONLY_TOOLS }] });
    expect(READ_ONLY_TOOLS.some((motif) => /create|update|delete|decide/.test(motif))).toBe(false);
  });

  it("a version is said in one line", () => {
    expect(versionSummary({})).toBe("defaults of the platform");
    expect(
      versionSummary({
        model: "profile:standard",
        skills: [{ slug: "a" }, { slug: "b" }],
        budget: { daily_usd: 5 },
        mcp_servers: [{ connector: "choregos", tools: ["list_*"] }],
      }),
    ).toBe("profile:standard · 2 skills · 1 tool pattern · $5/day");
  });

  it("a version's summary names its monthly cap", () => {
    expect(versionSummary({ budget: { monthly_usd: 100 } })).toBe("$100/month");
  });

  it("registering a Claude Code creates the external agent THEN attaches its token", async () => {
    vi.mocked(api.createAgent).mockResolvedValue({} as never);
    vi.mocked(api.attachAgentToken).mockResolvedValue({} as never);
    const token: ApiToken = {
      id: "tok-1",
      name: "Claude Code",
      created_at: "2026-10-05T10:00:00Z",
      last_used_at: "2026-10-05T11:57:00Z",
      last_client: "claude-code/2.1.0",
      scopes: ["mcp:write"],
    };
    render(
      <QueryClientProvider client={new QueryClient()}>
        <McpClients org="acme" clients={[token]} agents={[]} />
      </QueryClientProvider>,
    );
    expect(screen.getByText("Connected")).toBeInTheDocument();
    expect(screen.getByText(/claude-code\/2\.1\.0/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Register as an external agent" }));
    fireEvent.change(screen.getByLabelText("Tools"), { target: { value: "lecture" } });
    fireEvent.click(screen.getByRole("button", { name: "Register" }));
    await waitFor(() => expect(api.attachAgentToken).toHaveBeenCalledWith("acme", "claude-code", "tok-1"));
    expect(api.createAgent).toHaveBeenCalledWith("acme", {
      slug: "claude-code",
      kind: "external",
      display_name: "Claude Code",
      spec: versionDuClient(true),
    });
    expect(vi.mocked(api.createAgent).mock.invocationCallOrder[0]).toBeLessThan(
      vi.mocked(api.attachAgentToken).mock.invocationCallOrder[0]!,
    );
  });
});
