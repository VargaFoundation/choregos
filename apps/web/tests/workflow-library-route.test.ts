// @vitest-environment node
/**
 * The workflow catalogue is the workflow library (S55-04): "catalogue" read as French in the URL.
 * An old link, a bookmark or a page that still says `/workflows/catalogue` lands on the library, and
 * the map's list view is `?view=list` (it was `?vue=list`).
 */
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import config from "../next.config.mjs";

const APP = join(import.meta.dirname, "../src/app/p/[slug]/workflows");

describe("the workflow library's route", () => {
  it("is /workflows/library, and the old catalogue path redirects to it for good", async () => {
    expect(existsSync(join(APP, "library/page.tsx"))).toBe(true);
    expect(existsSync(join(APP, "catalogue"))).toBe(false);
    const redirects = await config.redirects!();
    expect(redirects).toContainEqual({
      source: "/p/:slug/workflows/catalogue",
      destination: "/p/:slug/workflows/library",
      permanent: true,
    });
  });

  it("writes ?view=list, and still reads an old ?vue=list", () => {
    const map = readFileSync(join(APP, "[name]/map/page.tsx"), "utf8");
    expect(map).toContain('params.get("view") ?? params.get("vue")');
    for (const page of ["library/page.tsx", "new/page.tsx", "[name]/map/page.tsx"]) {
      expect(readFileSync(join(APP, page), "utf8"), page).not.toMatch(/\?vue=/);
    }
  });
});
