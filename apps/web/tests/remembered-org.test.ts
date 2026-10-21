import { afterEach, describe, expect, it } from "vitest";
import { ORG_COOKIE, ORG_STORAGE_KEY, rememberOrgCookie, rememberedOrg, validOrgSlug } from "@/lib/remembered-org";

afterEach(() => {
  document.cookie = `${ORG_COOKIE}=; Path=/; Max-Age=0`;
  window.localStorage.clear();
});

describe("the remembered organisation (S42-13)", () => {
  it("accepts slugs and nothing else", () => {
    expect(validOrgSlug("acme-labs")).toBe("acme-labs");
    expect(validOrgSlug("0rg")).toBe("0rg");
    for (const bad of ["", "Acme", "a/b", "a;b", "-a", "a b", "a".repeat(64), 7, null, undefined]) {
      expect(validOrgSlug(bad)).toBeNull();
    }
  });

  it("sets a cookie the server can read, and refuses to write anything that is not a slug", () => {
    rememberOrgCookie("acme-labs");
    expect(document.cookie).toContain("choregos_org=acme-labs");
    rememberOrgCookie("x; Path=/; admin=1");
    expect(document.cookie).not.toContain("admin");
    expect(document.cookie).toContain("choregos_org=acme-labs");
  });

  it("reads the choice kept in the browser, validated", () => {
    expect(rememberedOrg()).toBeNull();
    window.localStorage.setItem(ORG_STORAGE_KEY, "acme-labs");
    expect(rememberedOrg()).toBe("acme-labs");
    window.localStorage.setItem(ORG_STORAGE_KEY, "../x");
    expect(rememberedOrg()).toBeNull();
  });
});
