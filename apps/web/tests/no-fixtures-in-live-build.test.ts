// @vitest-environment node
/**
 * A live build does not ship the demo fixtures (S30-04, bundle budget): `next.config.mjs` resolves
 * `@/mocks/data` to a stub outside demo mode, and the stub exports what the callers load. And the
 * chunking keeps small shared modules out of each route's chunk (they were copied up to nine times).
 */
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

const WEB = join(import.meta.dirname, "..");

async function config(mode: string) {
  vi.stubEnv("NEXT_PUBLIC_API_MODE", mode);
  vi.resetModules();
  return (await import("../next.config.mjs")).default as {
    turbopack?: { resolveAlias?: Record<string, string> };
    experimental?: { turbopackChunking?: { maxMergeChunkSize?: number; minChunkSize?: number } };
  };
}

describe("the demo fixtures in a live build", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("a live build resolves the fixtures to a stub that exports what their callers load", async () => {
    const alias = (await config("live")).turbopack?.resolveAlias?.["@/mocks/data"];
    expect(alias).toBe("./src/mocks/absent.ts");
    const stub = join(WEB, alias!);
    expect(existsSync(stub)).toBe(true);
    const source = readFileSync(stub, "utf8");
    for (const name of ["mockApi", "runEvents"]) expect(source).toMatch(new RegExp(`export (async )?(function|const) ${name}\\b`));
  });

  it("a small module shared by routes is not copied into each route's chunk (bundle budget)", async () => {
    const chunking = (await config("live")).experimental?.turbopackChunking;
    expect(chunking?.maxMergeChunkSize).toBeLessThanOrEqual(5_000);
    expect(chunking?.minChunkSize).toBeLessThanOrEqual(5_000);
  });

  it("the demo build keeps them", async () => {
    expect((await config("mock")).turbopack?.resolveAlias?.["@/mocks/data"]).toBeUndefined();
  });
});
