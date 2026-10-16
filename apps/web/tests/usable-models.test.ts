import { describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { usableModels } from "@/lib/usable-models";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, usableModels: vi.fn(), platformModels: vi.fn() } };
});

describe("the usable models of an organisation", () => {
  it("come from the catalogue when the API has one", async () => {
    vi.mocked(api.usableModels).mockResolvedValue([{ model_name: "a/b", display_name: "B" }]);
    expect(await usableModels("acme", { usage: "agent" })).toEqual([{ model_name: "a/b", display_name: "B" }]);
    expect(api.usableModels).toHaveBeenCalledWith("acme", { usage: "agent" });
  });

  it("fall back to the gateway's own list when the API predates the catalogue", async () => {
    vi.mocked(api.usableModels).mockRejectedValue(new Error("404"));
    vi.mocked(api.platformModels).mockResolvedValue([
      { model_name: "platform/x", litellm_model: "x", provider: "p", input_cost_per_1k: 1, output_cost_per_1k: 2 },
    ]);
    const [model] = await usableModels("acme");
    expect(model).toMatchObject({ model_name: "platform/x", priced_by: "gateway", input_cost_per_1k: 1 });
  });
});
