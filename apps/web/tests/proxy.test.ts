// @vitest-environment node
import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { proxy } from "@/proxy";

/** The CSP the console sends, directive by directive. */
async function directives(path = "/p/billing-api"): Promise<Map<string, string[]>> {
  const csp = (await proxy(new NextRequest(`https://choregos.example${path}`))).headers.get("Content-Security-Policy") ?? "";
  return new Map(
    csp
      .split(";")
      .map((d) => d.trim().split(/\s+/))
      .filter((pieces) => pieces[0])
      .map(([name, ...values]) => [name!, values]),
  );
}

describe("the content security policy (S21-05)", () => {
  it("opens no third-party origin to scripts, styles and fonts: everything the console loads comes from itself", async () => {
    const csp = await directives();
    for (const name of ["script-src", "style-src", "font-src", "worker-src", "default-src"]) {
      const foreign = (csp.get(name) ?? []).filter((v) => /^(https?:|\*|[a-z0-9.-]+\.[a-z]{2,})/i.test(v));
      expect(foreign, name).toEqual([]);
    }
  });

  it("keeps scripts under a nonce, without unsafe-inline", async () => {
    const scripts = (await directives()).get("script-src") ?? [];
    expect(scripts.some((v) => v.startsWith("'nonce-"))).toBe(true);
    expect(scripts).not.toContain("'unsafe-inline'");
  });

  it("lets an artifact's image be shown from its bytes, and nothing else from a blob (ADR 0049)", async () => {
    const csp = await directives();
    expect(csp.get("img-src")).toContain("blob:");
    expect(csp.get("media-src")).toContain("blob:");
    expect(csp.get("frame-src")).toContain("blob:");
    for (const name of ["script-src", "default-src", "object-src"]) expect(csp.get(name) ?? [], name).not.toContain("blob:");
  });
});

describe("the embedded component's frame-ancestors (S63-12)", () => {
  afterEach(() => vi.unstubAllGlobals());

  function api(answer: Response | Error) {
    const fetcher = vi.fn(async () => {
      if (answer instanceof Error) throw answer;
      return answer;
    });
    vi.stubGlobal("fetch", fetcher);
    return fetcher;
  }

  it("frames every other page by nobody", async () => {
    const fetcher = api(new Response("{}"));
    expect((await directives("/p/billing-api")).get("frame-ancestors")).toEqual(["'none'"]);
    expect((await directives("/embed/assistants/varga/coach/extra")).get("frame-ancestors")).toEqual(["'none'"]);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("lets the installation's origins, and only them, frame its embed page", async () => {
    const fetcher = api(Response.json({ embed_origins: ["https://app.varga.test", "http://localhost:3100"] }));
    const response = await proxy(new NextRequest("https://choregos.example/embed/assistants/varga/coach?context=e30"));
    expect(response.headers.get("Content-Security-Policy")).toContain(
      "frame-ancestors https://app.varga.test http://localhost:3100",
    );
    expect(String((fetcher.mock.calls as unknown as string[][])[0]![0])).toContain("/api/v1/public/assistants/varga/coach/embed");
  });

  it("drops anything that is not a plain origin, and frames nobody when the API is silent or says none", async () => {
    api(Response.json({ embed_origins: ["https://ok.test", "*", "https://x.test; script-src *"] }));
    expect((await directives("/embed/assistants/varga/coach")).get("frame-ancestors")).toEqual(["https://ok.test"]);
    api(Response.json({ embed_origins: [] }));
    expect((await directives("/embed/assistants/varga/coach")).get("frame-ancestors")).toEqual(["'none'"]);
    api(new Response("nope", { status: 404 }));
    expect((await directives("/embed/assistants/varga/coach")).get("frame-ancestors")).toEqual(["'none'"]);
    api(new Error("down"));
    expect((await directives("/embed/assistants/varga/coach")).get("frame-ancestors")).toEqual(["'none'"]);
  });
});
