import { describe, expect, it } from "vitest";
import { estUneReference, connectorRows } from "@/components/connectors";
import type { ConnectorDto, ProjectRequirement } from "@/lib/types";

const exige = (...capacites: string[]): ProjectRequirement[] =>
  capacites.map((capability) => ({ capability, reasons: [`${capability} needed`], connector: null }));

describe("a project's connectors (ADR 0034, S19-01)", () => {
  it("an HR project sees neither scm, nor ci, nor cd: only what its workflows require", () => {
    expect(connectorRows(exige("tracker", "runtime", "gateway"), [])).toEqual(["tracker", "runtime", "gateway"]);
  });

  it("a configured connector shows even if nothing requires it, in the order of the capabilities", () => {
    const notify: ConnectorDto = { kind: "notify", type: "slack", config: {}, status: "ok" } as ConnectorDto;
    const identity: ConnectorDto = { kind: "identity", type: "entra", config: {}, status: "unknown" } as ConnectorDto;
    expect(connectorRows(exige("gateway", "tracker", "scm"), [notify, identity])).toEqual([
      "tracker",
      "scm",
      "gateway",
      "notify",
      "identity",
    ]);
  });

  it("a secret is written as a reference, never as a value", () => {
    expect(estUneReference("env:CONNECTOR_JIRA_TOKEN")).toBe(true);
    expect(estUneReference("vault:kv/jira#token")).toBe(true);
    expect(estUneReference("s3cr3t")).toBe(false);
    expect(estUneReference("ghp_0123456789abcdef")).toBe(false);
  });
});
