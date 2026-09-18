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

  it("construit un lien d'export CSV téléchargeable tel quel", async () => {
    const { api } = await import("@/lib/api");
    expect(api.costsCsvUrl("billing-api", "stage")).toBe(
      "/api/v1/projects/billing-api/costs.csv?group_by=stage",
    );
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
