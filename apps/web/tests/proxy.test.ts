// @vitest-environment node
import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";
import { proxy } from "@/proxy";

/** The CSP the console sends, directive by directive. */
function directives(): Map<string, string[]> {
  const csp = proxy(new NextRequest("https://choregos.example/p/billing-api")).headers.get("Content-Security-Policy") ?? "";
  return new Map(
    csp
      .split(";")
      .map((d) => d.trim().split(/\s+/))
      .filter((pieces) => pieces[0])
      .map(([name, ...values]) => [name!, values]),
  );
}

describe("the content security policy (S21-05)", () => {
  it("opens no third-party origin to scripts, styles and fonts: everything the console loads comes from itself", () => {
    const csp = directives();
    for (const name of ["script-src", "style-src", "font-src", "worker-src", "default-src"]) {
      const foreign = (csp.get(name) ?? []).filter((v) => /^(https?:|\*|[a-z0-9.-]+\.[a-z]{2,})/i.test(v));
      expect(foreign, name).toEqual([]);
    }
  });

  it("keeps scripts under a nonce, without unsafe-inline", () => {
    const scripts = directives().get("script-src") ?? [];
    expect(scripts.some((v) => v.startsWith("'nonce-"))).toBe(true);
    expect(scripts).not.toContain("'unsafe-inline'");
  });

  it("lets an artifact's image be shown from its bytes, and nothing else from a blob (ADR 0049)", () => {
    const csp = directives();
    expect(csp.get("img-src")).toContain("blob:");
    expect(csp.get("media-src")).toContain("blob:");
    expect(csp.get("frame-src")).toContain("blob:");
    for (const name of ["script-src", "default-src", "object-src"]) expect(csp.get(name) ?? [], name).not.toContain("blob:");
  });
});
