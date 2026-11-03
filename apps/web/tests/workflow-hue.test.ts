// @vitest-environment node
/**
 * A workflow wears one colour wherever it appears (ADR 0120): computed from its name, stable, and
 * spread over the eight hues so that a project's few workflows rarely share one.
 */
import { describe, expect, it } from "vitest";
import {
  HUE_CLASSES,
  HUES,
  workflowHue,
  workflowHueClasses,
} from "@/lib/workflow-hue";

const NAMES = [
  "default-simple",
  "default-request",
  "default-delivery",
  "blank",
  "fon-access",
  "fon-onboarding",
  "hr-leave",
  "hr-hiring",
  "it-access",
  "it-incident",
  "billing-dispute",
  "security-review",
  "release",
  "hotfix",
  "procurement",
  "legal-review",
];

describe("workflowHue", () => {
  it("gives the same hue to the same name, whatever its case or surrounding spaces", () => {
    expect(workflowHue("fon-access")).toBe(workflowHue("fon-access"));
    expect(workflowHue(" Fon-Access ")).toBe(workflowHue("fon-access"));
  });

  it("is one of the eight hues", () => {
    for (const name of NAMES) expect(HUES).toContain(workflowHue(name));
  });

  it("spreads names over most hues", () => {
    expect(new Set(NAMES.map(workflowHue)).size).toBeGreaterThanOrEqual(6);
  });

  it("tells apart the core workflows a new project starts with", () => {
    const core = [
      "default-simple",
      "default-request",
      "default-delivery",
      "blank",
    ];
    expect(new Set(core.map(workflowHue)).size).toBeGreaterThanOrEqual(3);
  });

  it("hands the classes of its hue", () => {
    const hue = workflowHue("fon-access");
    expect(workflowHueClasses("fon-access")).toBe(HUE_CLASSES[hue]);
    expect(HUE_CLASSES[hue].pill).toContain(`bg-cat-${hue}-soft`);
  });
});
