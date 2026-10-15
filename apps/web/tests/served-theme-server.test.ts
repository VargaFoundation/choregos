// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SAMPLE_THEME } from "@/mocks/theme-sample";

vi.mock("next/headers", () => ({ cookies: async () => ({ get: () => undefined }) }));

beforeEach(async () => {
  vi.resetModules();
  vi.stubEnv("NEXT_PUBLIC_API_MODE", "live");
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("the instance theme, read on the server (S39-03b)", () => {
  it("fetches /api/v1/theme with a timeout and keeps it for a minute", async () => {
    vi.stubEnv("CHOREGOS_API_URL", "http://api.test");
    const fetcher = vi.fn(async () => new Response(JSON.stringify(SAMPLE_THEME)));
    vi.stubGlobal("fetch", fetcher);
    const { instanceTheme } = await import("@/lib/served-theme-server");
    expect((await instanceTheme()).product_name).toBe("Sample");
    expect((await instanceTheme()).product_name).toBe("Sample");
    expect(fetcher).toHaveBeenCalledTimes(1);
    const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://api.test/api/v1/theme");
    expect(init.signal).toBeInstanceOf(AbortSignal);
  });

  it.each([
    ["an API that is down", () => Promise.reject(new Error("ECONNREFUSED"))],
    ["an error answer", async () => new Response("boom", { status: 500 })],
    ["a body that is not a theme", async () => new Response('"hello"')],
    ["a body that is not JSON", async () => new Response("<html>")],
  ])("gives the default theme for %s", async (_name, impl) => {
    vi.stubGlobal("fetch", vi.fn(impl));
    const { instanceTheme } = await import("@/lib/served-theme-server");
    expect((await instanceTheme()).id).toBe("default");
  });
});
