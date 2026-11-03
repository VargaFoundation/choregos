import { describe, expect, it } from "vitest";
import * as fixtures from "@/mocks/data";

// The demo mode is the console's showcase: everything it shows is in English, like the interface
// (review of 07/10: "English only, for the whole platform and for the demo").
// Proper names keep their accents.
const PROPER_NAMES = ["Léa"]; // lang: fr-ok
const ACCENTS = /[àâçéèêëîïôûùüÿœ]/i; // lang: fr-ok
const FRENCH_WORDS = /\b(les|des|une|est|vers|pour|dans|avec|aux|pas|sont|nous|votre|tentative|périmètre)\b/i; // lang: fr-ok

/** Every string of a value, with its path, keys included when they read on screen. */
function strings(value: unknown, path: string, seen = new Set<unknown>()): [string, string][] {
  if (typeof value === "string") return [[path, value]];
  if (typeof value !== "object" || value === null || seen.has(value)) return [];
  seen.add(value);
  return Object.entries(value).flatMap(([key, v]) => strings(v, `${path}.${key}`, seen));
}

function looksFrench(text: string): boolean {
  const withoutNames = PROPER_NAMES.reduce((t, name) => t.replaceAll(name, ""), text);
  return ACCENTS.test(withoutNames) || FRENCH_WORDS.test(withoutNames);
}

describe("the demo mode's fixtures", () => {
  it("the detector recognises French and lets English and proper names through", () => {
    expect(looksFrench("Les avoirs ne sont pas déduits du total")).toBe(true); // lang: fr-ok
    expect(looksFrench("hors du périmètre autorisé")).toBe(true); // lang: fr-ok
    expect(looksFrench("refine — tentative 1")).toBe(true); // lang: fr-ok
    expect(looksFrench("Order Léa's laptop (supplier)")).toBe(false);
    expect(looksFrench("Credit notes are not deducted from the total")).toBe(false);
  });

  it("show no French", () => {
    const faults = Object.entries(fixtures)
      .filter(([, value]) => typeof value !== "function")
      .flatMap(([name, value]) => strings(value, name))
      .filter(([, text]) => looksFrench(text))
      .map(([path, text]) => `${path}: ${text.slice(0, 80)}`);
    expect(faults).toEqual([]);
  });
});
