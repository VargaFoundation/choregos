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
});
