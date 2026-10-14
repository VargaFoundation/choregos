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
    await api.projects("acme");
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/orgs/acme/projects", expect.objectContaining({ credentials: "include" }));
    vi.unstubAllGlobals();
  });

  it("construit un lien d'export CSV téléchargeable tel quel, qualifié par l'organisation (#178)", async () => {
    const { api, setCurrentOrg } = await import("@/lib/api");
    expect(api.costsCsvUrl("billing-api", "stage")).toBe(
      "/api/v1/projects/default:billing-api/costs.csv?group_by=stage",
    );
    setCurrentOrg("acme");
    try {
      expect(api.costsCsvUrl("billing-api")).toBe("/api/v1/projects/acme:billing-api/costs.csv?group_by=day");
    } finally {
      setCurrentOrg("");
    }
  });

  it("starts on an organisation that is not the foundation's (ADR 0048)", async () => {
    const { defaultOrg, DEFAULT_ORG } = await import("@/lib/api");
    expect(DEFAULT_ORG).not.toBe("varga");
    expect(defaultOrg(undefined, true)).toBe("acme");
    expect(defaultOrg(undefined, false)).toBe("default");
    expect(defaultOrg("  ", false)).toBe("default");
    expect(defaultOrg("globex", true)).toBe("globex");
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
      location: { pathname: "/p/infra/actions/pr1", search: "", origin: "http://c", assign },
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
    await expect(api.decideAction("infra", "pr1", { decision: "approve" })).rejects.toThrow();
    expect(assign).toHaveBeenCalledWith("http://c/api/v1/auth/login?redirect_to=%2Fp%2Finfra%2Factions%2Fpr1&reauth=1");
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

describe("unhandled API errors (S26-02)", () => {
  it("shows the problem detail and the correlation id of a problem+json 500", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              title: "Internal server error",
              status: 500,
              detail: "an unexpected error occurred; quote the correlation id abc123 to the operators",
              correlation_id: "abc123",
            }),
            { status: 500, headers: { "Content-Type": "application/problem+json", "X-Request-Id": "abc123" } },
          ),
      ),
    );
    const { api, ApiError } = await import("@/lib/api");
    const error = await api.orgs().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    const apiError = error as InstanceType<typeof ApiError>;
    expect(apiError.message).toBe("an unexpected error occurred; quote the correlation id abc123 to the operators");
    expect(apiError.correlationId).toBe("abc123");
    vi.unstubAllGlobals();
  });

  it("a plain-text 500 still carries the request id the API answered", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response("Internal Server Error", {
            status: 500,
            statusText: "Internal Server Error",
            headers: { "Content-Type": "text/plain", "X-Request-Id": "req-9" },
          }),
      ),
    );
    const { api } = await import("@/lib/api");
    await expect(api.orgs()).rejects.toThrow("Internal Server Error (correlation id req-9)");
    vi.unstubAllGlobals();
  });
});
