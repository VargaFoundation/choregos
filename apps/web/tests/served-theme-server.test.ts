// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SAMPLE_THEME } from "@/mocks/theme-sample";

let cookie: string | undefined;
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: (name: string) => (name === "choregos_org" && cookie ? { value: cookie } : undefined) }),
}));

beforeEach(async () => {
  cookie = undefined;
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

  it("asks for the remembered organisation's theme, and keeps one memo per organisation (S42-13)", async () => {
    vi.stubEnv("CHOREGOS_API_URL", "http://api.test");
    const fetcher = vi.fn(async (url: string, _init?: RequestInit) =>
      new Response(JSON.stringify(url.endsWith("?org=acme-labs") ? { ...SAMPLE_THEME, id: "acme-labs", product_name: "Acme Labs" } : SAMPLE_THEME)),
    );
    vi.stubGlobal("fetch", fetcher);
    const { instanceTheme } = await import("@/lib/served-theme-server");
    cookie = "acme-labs";
    expect((await instanceTheme()).product_name).toBe("Acme Labs");
    cookie = undefined;
    expect((await instanceTheme()).product_name).toBe("Sample");
    cookie = "acme-labs";
    expect((await instanceTheme()).product_name).toBe("Acme Labs");
    expect(fetcher.mock.calls.map((call) => (call as unknown as [string])[0])).toEqual([
      "http://api.test/api/v1/theme?org=acme-labs",
      "http://api.test/api/v1/theme",
    ]);
  });

  it.each(["../admin", "Acme", "a b", "acme?x=1", "-acme", "a".repeat(64), "acme/../x"])(
    "never sends a cookie value that is not a slug (%s)",
    async (value) => {
      vi.stubEnv("CHOREGOS_API_URL", "http://api.test");
      const fetcher = vi.fn(async () => new Response(JSON.stringify(SAMPLE_THEME)));
      vi.stubGlobal("fetch", fetcher);
      const { instanceTheme } = await import("@/lib/served-theme-server");
      cookie = value;
      await instanceTheme();
      expect((fetcher.mock.calls[0] as unknown as [string])[0]).toBe("http://api.test/api/v1/theme");
    },
  );

  it("bounds the memo: invented cookie values cannot grow it without limit", async () => {
    vi.stubEnv("CHOREGOS_API_URL", "http://api.test");
    const fetcher = vi.fn(async () => new Response(JSON.stringify(SAMPLE_THEME)));
    vi.stubGlobal("fetch", fetcher);
    const { instanceTheme } = await import("@/lib/served-theme-server");
    for (let i = 0; i < 40; i++) {
      cookie = `org-${i}`;
      await instanceTheme();
    }
    // The first ones were evicted: asking again fetches again; the last one is still remembered.
    cookie = "org-0";
    await instanceTheme();
    expect(fetcher).toHaveBeenCalledTimes(41);
    cookie = "org-39";
    await instanceTheme();
    expect(fetcher).toHaveBeenCalledTimes(41);
  });

  it("in the demo build, a remembered organisation with a sample theme is drawn at the first paint", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_MODE", "mock");
    const { instanceTheme } = await import("@/lib/served-theme-server");
    cookie = "acme-labs";
    expect((await instanceTheme()).product_name).toBe("Acme Labs");
    cookie = "acme";
    expect((await instanceTheme()).id).toBe("default");
  });
});
