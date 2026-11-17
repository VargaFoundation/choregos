// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SAMPLE_THEME } from "@/mocks/theme-sample";

let cookie: string | undefined;
let incoming: Record<string, string> = {};
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: (name: string) => (name === "choregos_org" && cookie ? { value: cookie } : undefined) }),
  headers: async () => ({ get: (name: string) => incoming[name] ?? null }),
}));

beforeEach(async () => {
  cookie = undefined;
  incoming = {};
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

describe("a host an organisation owns lands on that organisation, theme first (ADR 0175)", () => {
  const ACME = { ...SAMPLE_THEME, id: "acme", product_name: "Acme Agentic" };
  /** An API that knows one tenant host, like the enterprise edition's resolver would. */
  function api() {
    return vi.fn(async (_url: string, init?: RequestInit) => {
      const forwarded = new Headers(init?.headers).get("x-forwarded-host");
      return forwarded === "agentic.client.com"
        ? new Response(JSON.stringify(ACME), { headers: { "x-choregos-host-org": "acme" } })
        : new Response(JSON.stringify(SAMPLE_THEME));
    });
  }

  it("forwards the visitor's host to the API and draws the host's organisation, with its name", async () => {
    vi.stubEnv("CHOREGOS_API_URL", "http://api.test");
    const fetcher = api();
    vi.stubGlobal("fetch", fetcher);
    incoming = { "x-forwarded-host": "Agentic.Client.com:443", host: "web.svc:3000" };
    const { servedLook } = await import("@/lib/served-theme-server");
    const look = await servedLook();
    expect(look.theme.product_name).toBe("Acme Agentic");
    expect(look.hostOrg).toBe("acme");
    const [, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    expect(new Headers(init.headers).get("x-forwarded-host")).toBe("agentic.client.com");
  });

  it("prefers the host's organisation over the remembered one, and keys the memo by host", async () => {
    vi.stubEnv("CHOREGOS_API_URL", "http://api.test");
    const fetcher = api();
    vi.stubGlobal("fetch", fetcher);
    incoming = { host: "agentic.client.com" };
    const { servedLook } = await import("@/lib/served-theme-server");
    cookie = "other-org";
    expect((await servedLook()).hostOrg).toBe("acme");
    // Another remembered organisation on the same host: served from the host's own entry, no new fetch.
    cookie = "third-org";
    const again = await servedLook();
    expect(again.theme.product_name).toBe("Acme Agentic");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("never serves a tenant host's look to another host, nor the instance's look to the tenant host", async () => {
    vi.stubEnv("CHOREGOS_API_URL", "http://api.test");
    vi.stubGlobal("fetch", api());
    const { servedLook } = await import("@/lib/served-theme-server");
    incoming = { host: "agentic.client.com" };
    expect((await servedLook()).theme.product_name).toBe("Acme Agentic");
    incoming = { host: "console.example.com" };
    const instance = await servedLook();
    expect(instance.theme.product_name).toBe("Sample");
    expect(instance.hostOrg).toBeNull();
    incoming = { host: "agentic.client.com" };
    expect((await servedLook()).theme.product_name).toBe("Acme Agentic");
  });

  it.each(["10.0.0.1", "localhost:3000", "a b.example.com", "x/../y.example.com"])(
    "forwards nothing that is not a DNS name (%s)",
    async (host) => {
      vi.stubEnv("CHOREGOS_API_URL", "http://api.test");
      const fetcher = api();
      vi.stubGlobal("fetch", fetcher);
      incoming = { host };
      const { servedLook } = await import("@/lib/served-theme-server");
      await servedLook();
      const [, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
      expect(new Headers(init.headers).get("x-forwarded-host")).toBeNull();
    },
  );

  it("takes the organisation from the header only when it is a slug", async () => {
    vi.stubEnv("CHOREGOS_API_URL", "http://api.test");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(SAMPLE_THEME), { headers: { "x-choregos-host-org": "../Admin" } })),
    );
    incoming = { host: "agentic.client.com" };
    const { servedLook } = await import("@/lib/served-theme-server");
    expect((await servedLook()).hostOrg).toBeNull();
  });
});

