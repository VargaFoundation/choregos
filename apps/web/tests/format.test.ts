import { describe, expect, it } from "vitest";
import { duration, eur, percent, tokens } from "@/lib/format";

describe("formatage", () => {
  it("écrit les montants en français", () => {
    expect(eur(6.77)).toMatch(/6,77/);
    expect(eur(null)).toBe("—");
  });

  it("abrège les tokens", () => {
    expect(tokens(388_000)).toBe("388 k");
    expect(tokens(1_250_000)).toBe("1,3 M");
    expect(tokens(0)).toBe("0");
  });

  it("rend les durées lisibles", () => {
    expect(duration(45)).toBe("45 s");
    expect(duration(1260)).toBe("21 min");
    expect(duration(3600 * 27)).toBe("1 j 3 h");
  });

  it("arrondit les pourcentages", () => {
    expect(percent(0.723)).toBe("72 %");
    expect(percent(undefined)).toBe("—");
  });
});
