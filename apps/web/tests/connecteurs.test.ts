import { describe, expect, it } from "vitest";
import { estUneReference, lignesDeConnecteurs } from "@/components/connecteurs";
import type { ConnectorDto, ProjectRequirement } from "@/lib/types";

const exige = (...capacites: string[]): ProjectRequirement[] =>
  capacites.map((capability) => ({ capability, reasons: [`${capability} needed`], connector: null }));

describe("les connecteurs d'un projet (ADR 0034, S19-01)", () => {
  it("un projet RH ne voit ni scm, ni ci, ni cd : seulement ce que ses workflows exigent", () => {
    expect(lignesDeConnecteurs(exige("tracker", "runtime", "gateway"), [])).toEqual(["tracker", "runtime", "gateway"]);
  });

  it("un connecteur configuré se montre même si rien ne l'exige, dans l'ordre des capacités", () => {
    const notify: ConnectorDto = { kind: "notify", type: "slack", config: {}, status: "ok" } as ConnectorDto;
    const identite: ConnectorDto = { kind: "identity", type: "entra", config: {}, status: "unknown" } as ConnectorDto;
    expect(lignesDeConnecteurs(exige("gateway", "tracker", "scm"), [notify, identite])).toEqual([
      "tracker",
      "scm",
      "gateway",
      "notify",
      "identity",
    ]);
  });

  it("un secret s'écrit en référence, jamais en valeur", () => {
    expect(estUneReference("env:JIRA_TOKEN")).toBe(true);
    expect(estUneReference("vault:kv/jira#token")).toBe(true);
    expect(estUneReference("s3cr3t")).toBe(false);
    expect(estUneReference("ghp_0123456789abcdef")).toBe(false);
  });
});
