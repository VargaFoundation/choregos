import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ClientsMcp, OUTILS_EN_LECTURE, versionDuClient } from "@/components/agents/clients-mcp";
import {
  agentsImplicites,
  estUnClientMcp,
  etatDuClient,
  resumeDeLaVersion,
  slugDeLActeur,
  versionDeLActeur,
} from "@/components/agents/registre";
import { api } from "@/lib/api";
import type { ApiToken } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
    api: { ...reel.api, createAgent: vi.fn(), attachAgentToken: vi.fn(), agentCredentials: vi.fn() },
  };
});

describe("les agents implicites d'un projet (S18-07)", () => {
  const workflows = [
    {
      name: "onboarding",
      json: {
        actors: {
          coordinateur: { type: "agent", role: "plan", agent: "coordinateur-onboarding@2" },
          redacteur: { type: "agent", role: "refine", model: "profile:standard", max_turns: 30 },
          oublie: { type: "agent", role: "verify", agent: "absent" },
          rh: { type: "human", group: "rh" },
        },
      },
    },
  ];

  it("un acteur agent qui ne nomme aucun agent du registre — ou un agent absent — est implicite", () => {
    const implicites = agentsImplicites(workflows, new Set(["coordinateur-onboarding"]));
    expect(implicites.map((i) => i.acteur)).toEqual(["redacteur", "oublie"]);
    expect(implicites[0]).toMatchObject({ workflow: "onboarding", role: "refine", model: "profile:standard" });
  });

  it("enregistré, il garde ce que le workflow disait de lui, et rien de plus", () => {
    const redacteur = agentsImplicites(workflows, new Set()).find((i) => i.acteur === "redacteur");
    expect(versionDeLActeur(redacteur!)).toEqual({ model: "profile:standard", limits: { max_turns: 30, max_minutes: null } });
    expect(slugDeLActeur("Rédacteur RH_2")).toBe("r-dacteur-rh-2");
  });
});

describe("les clients MCP", () => {
  it("un jeton qui ouvre la porte MCP est un client ; un jeton large ne l'est pas", () => {
    expect(estUnClientMcp({ scopes: ["mcp:read"] })).toBe(true);
    expect(estUnClientMcp({ scopes: ["*"] })).toBe(false);
  });

  it("connecté, seulement s'il a appelé — et plus du tout une fois révoqué", () => {
    const maintenant = new Date("2026-10-05T12:00:00Z");
    expect(etatDuClient({ last_used_at: null }, maintenant)).toEqual({ connecte: false, recent: false });
    expect(etatDuClient({ last_used_at: "2026-10-05T11:57:00Z" }, maintenant)).toEqual({ connecte: true, recent: true });
    expect(etatDuClient({ last_used_at: "2026-10-01T11:57:00Z" }, maintenant)).toEqual({ connecte: true, recent: false });
    expect(etatDuClient({ last_used_at: "2026-10-05T11:57:00Z", revoked_at: "2026-10-05T11:58:00Z" }, maintenant).connecte).toBe(
      false,
    );
  });

  it("en lecture seule, un agent externe ne nomme que des outils qui lisent", () => {
    expect(versionDuClient(false)).toEqual({});
    expect(versionDuClient(true)).toEqual({ mcp_servers: [{ connector: "choregos", tools: OUTILS_EN_LECTURE }] });
    expect(OUTILS_EN_LECTURE.some((motif) => /create|update|delete|decide/.test(motif))).toBe(false);
  });

  it("une version se dit en une ligne", () => {
    expect(resumeDeLaVersion({})).toBe("defaults of the platform");
    expect(
      resumeDeLaVersion({
        model: "profile:standard",
        skills: [{ slug: "a" }, { slug: "b" }],
        budget: { daily_usd: 5 },
        mcp_servers: [{ connector: "choregos", tools: ["list_*"] }],
      }),
    ).toBe("profile:standard · 2 skills · 1 tool pattern · $5/day");
  });

  it("enregistrer un Claude Code crée l'agent externe PUIS lui rattache son jeton", async () => {
    vi.mocked(api.createAgent).mockResolvedValue({} as never);
    vi.mocked(api.attachAgentToken).mockResolvedValue({} as never);
    const jeton: ApiToken = {
      id: "tok-1",
      name: "Claude Code",
      created_at: "2026-10-05T10:00:00Z",
      last_used_at: "2026-10-05T11:57:00Z",
      last_client: "claude-code/2.1.0",
      scopes: ["mcp:write"],
    };
    render(
      <QueryClientProvider client={new QueryClient()}>
        <ClientsMcp org="varga" clients={[jeton]} agents={[]} />
      </QueryClientProvider>,
    );
    expect(screen.getByText("connected")).toBeInTheDocument();
    expect(screen.getByText(/claude-code\/2\.1\.0/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "register as an external agent" }));
    fireEvent.change(screen.getByLabelText("tools"), { target: { value: "lecture" } });
    fireEvent.click(screen.getByRole("button", { name: "register" }));
    await waitFor(() => expect(api.attachAgentToken).toHaveBeenCalledWith("varga", "claude-code", "tok-1"));
    expect(api.createAgent).toHaveBeenCalledWith("varga", {
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
