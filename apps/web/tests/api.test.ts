import { describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";

describe("client API", () => {
  it("turns an RFC 9457 error into a readable message", () => {
    const error = new ApiError(422, { title: "Unprocessable entity", detail: "invalid workflow" });
    expect(error.message).toBe("invalid workflow");
    expect(error.status).toBe(422);
  });

  it("calls the contract's paths", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({ items: [], meta: { has_more: false } }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { api } = await import("@/lib/api");
    await api.projects("acme");
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/orgs/acme/projects", expect.objectContaining({ credentials: "include" }));
    vi.unstubAllGlobals();
  });

  it("builds a CSV export link that downloads as it is, qualified by the organisation (#178)", async () => {
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

  it("serves the DORA measures in mock mode", async () => {
    const { mockApi } = await import("@/mocks/data");
    const report = await mockApi<{ deployments: number; lead_time: { level: string } }>(
      "/projects/billing-api/metrics/dora?env=prod",
    );
    expect(report.deployments).toBeGreaterThan(0);
    expect(report.lead_time.level).toBe("elite");
  });
});

describe("a decision that asks for a recent authentication", () => {
  it("a 401 step_up_required goes back through the IdP with reauth=1, and comes back here", async () => {
    const assign = vi.fn();
    vi.stubGlobal("window", {
      location: { pathname: "/p/infra/actions/pr1", search: "", origin: "http://c", assign },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({ title: "Decision refused", errors: [{ error: "step_up_required", reauth: "GET …" }] }),
            { status: 401 },
          ),
      ),
    );
    const { api } = await import("@/lib/api");
    await expect(api.decideAction("infra", "pr1", { decision: "approve" })).rejects.toThrow();
    expect(assign).toHaveBeenCalledWith("http://c/api/v1/auth/login?redirect_to=%2Fp%2Finfra%2Factions%2Fpr1&reauth=1");
    vi.unstubAllGlobals();
  });

  it("an ordinary 401 still leads to the sign-in page", async () => {
    const assign = vi.fn();
    vi.stubGlobal("window", { location: { pathname: "/p/infra", search: "", origin: "http://c", assign } });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ title: "Not authenticated" }), { status: 401 })),
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

describe("section upload (S42-13)", () => {
  it("posts the file as the raw body with its own content type, and answers the ref", async () => {
    const fetchMock = vi.fn(async (..._args: unknown[]) => new Response(JSON.stringify({ ref: "/api/v1/x/logo.png" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    try {
      const { api } = await import("@/lib/api");
      const file = new File(["png-bytes"], "logo.png", { type: "image/png" });
      expect(await api.sectionUpload("/orgs/acme/theme/ressources", file)).toEqual({ ref: "/api/v1/x/logo.png" });
      const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
      expect(url).toBe("/api/v1/orgs/acme/theme/ressources");
      expect(init.method).toBe("POST");
      expect(init.body).toBe(file);
      expect(init.headers).toEqual({ "Content-Type": "image/png" });
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("falls back to octet-stream for a file without a type", async () => {
    const fetchMock = vi.fn(async (..._args: unknown[]) => new Response(JSON.stringify({ ref: "r" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    try {
      const { api } = await import("@/lib/api");
      await api.sectionUpload("/orgs/acme/up", new File(["x"], "blob"));
      const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
      expect(init.headers).toEqual({ "Content-Type": "application/octet-stream" });
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
