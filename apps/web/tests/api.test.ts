import { describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";

describe("client API", () => {
  it("transforme une erreur RFC 9457 en message lisible", () => {
    const error = new ApiError(422, { title: "Entité non traitable", detail: "workflow invalide" });
    expect(error.message).toBe("workflow invalide");
    expect(error.status).toBe(422);
  });

  it("appelle les chemins du contrat", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({ items: [], meta: { has_more: false } }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { api } = await import("@/lib/api");
    await api.projects("varga");
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/orgs/varga/projects", expect.objectContaining({ credentials: "include" }));
    vi.unstubAllGlobals();
  });

  it("construit un lien d'export CSV téléchargeable tel quel, qualifié par l'organisation (#178)", async () => {
    const { api, setCurrentOrg } = await import("@/lib/api");
    expect(api.costsCsvUrl("billing-api", "stage")).toBe(
      "/api/v1/projects/varga:billing-api/costs.csv?group_by=stage",
    );
    setCurrentOrg("acme");
    try {
      expect(api.costsCsvUrl("billing-api")).toBe("/api/v1/projects/acme:billing-api/costs.csv?group_by=day");
    } finally {
      setCurrentOrg("");
    }
  });

  it("sert les mesures DORA en mode maquette", async () => {
    const { mockApi } = await import("@/mocks/data");
    const report = await mockApi<{ deployments: number; lead_time: { level: string } }>(
      "/projects/billing-api/metrics/dora?env=prod",
    );
    expect(report.deployments).toBeGreaterThan(0);
    expect(report.lead_time.level).toBe("elite");
  });
});

describe("décision qui demande une authentification récente", () => {
  it("un 401 step_up_required repasse par l'IdP avec reauth=1, et revient ici", async () => {
    const assign = vi.fn();
    vi.stubGlobal("window", {
      location: { pathname: "/p/infra/proposals/pr1", search: "", origin: "http://c", assign },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({ title: "Décision refusée", errors: [{ error: "step_up_required", reauth: "GET …" }] }),
            { status: 401 },
          ),
      ),
    );
    const { api } = await import("@/lib/api");
    await expect(api.decideProposal("infra", "pr1", { decision: "approve" })).rejects.toThrow();
    expect(assign).toHaveBeenCalledWith("http://c/api/v1/auth/login?redirect_to=%2Fp%2Finfra%2Fproposals%2Fpr1&reauth=1");
    vi.unstubAllGlobals();
  });

  it("un 401 ordinaire mène toujours à la page de connexion", async () => {
    const assign = vi.fn();
    vi.stubGlobal("window", { location: { pathname: "/p/infra", search: "", origin: "http://c", assign } });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ title: "Non authentifié" }), { status: 401 })),
    );
    const { api } = await import("@/lib/api");
    await expect(api.me()).rejects.toThrow();
    expect(assign).toHaveBeenCalledWith("http://c/login?next=%2Fp%2Finfra");
    vi.unstubAllGlobals();
  });
});
