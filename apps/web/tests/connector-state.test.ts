import { describe, expect, it } from "vitest";
import { connectorState, isStandIn, relativeTime } from "@/lib/connector-state";
import type { ConnectorDto, ProjectRequirement } from "@/lib/types";

const connector = (extra: Partial<ConnectorDto> = {}) =>
  ({ kind: "cd", type: "argocd", config: {}, status: "ok", ...extra }) as ConnectorDto;
const requirement = (extra: Partial<ProjectRequirement> = {}): ProjectRequirement => ({
  capability: "cd",
  reasons: ["default-simple: the guarantee deployed on t-ship"],
  connector: null,
  default_type: null,
  ...extra,
});
const NOW = Date.parse("2026-10-16T12:00:00Z");

describe("what a connector's state says (S40-05)", () => {
  it("ok reads Connected, with when it was tested", () => {
    const state = connectorState({ connector: connector({ last_check_at: "2026-10-16T11:57:00Z" }), required: true });
    expect(state).toMatchObject({ label: "Connected", tone: "ok" });
    expect(state?.detail).toMatch(/^tested \d+ (min|h|d) ago$|^tested just now$/);
  });

  it("error reads Failing with the error, cut at 200 characters, and offers a new test", () => {
    const state = connectorState({ connector: connector({ status: "error", last_error: "x".repeat(500) }), required: true });
    expect(state).toMatchObject({ label: "Failing", tone: "danger", action: "test-again" });
    expect(state?.detail).toHaveLength(200);
  });

  it("degraded reads Degraded with the error", () => {
    expect(connectorState({ connector: connector({ status: "degraded", last_error: "slow" }), required: true })).toMatchObject({
      label: "Degraded",
      tone: "warn",
      detail: "slow",
    });
  });

  it("unknown reads Not tested yet and offers a test", () => {
    expect(connectorState({ connector: connector({ status: "unknown" }), required: true })).toMatchObject({
      label: "Not tested yet",
      tone: "warn",
      action: "test",
    });
  });

  it("a demo or fake type is a stand-in, and says what it simulates", () => {
    const state = connectorState({ connector: connector({ type: "demo" }), required: true });
    expect(state?.standIn).toBe(true);
    expect(state?.detail).toContain("simulates cd; nothing real is deployed");
    expect(isStandIn("fake")).toBe(true);
    expect(isStandIn("argocd")).toBe(false);
  });

  it("not configured and provided by the deployment: Provided by the platform, no status", () => {
    expect(connectorState({ requirement: requirement({ capability: "runtime", default_type: "k8s_job" }), required: true })).toEqual({
      label: "Provided by the platform",
      tone: "neutral",
      detail: "k8s_job",
      standIn: false,
    });
  });

  it("not configured, required, not provided: Not connected with the first reason and a way to connect", () => {
    expect(connectorState({ requirement: requirement(), required: true })).toMatchObject({
      label: "Not connected",
      tone: "danger",
      detail: "default-simple: the guarantee deployed on t-ship",
      action: "connect",
    });
  });

  it("not configured and not required: nothing to show", () => {
    expect(connectorState({ required: false })).toBeNull();
  });

  it('the raw word "unknown" never reaches a label or a detail', () => {
    for (const status of ["ok", "error", "degraded", "unknown", "something-new"]) {
      const state = connectorState({ connector: connector({ status: status as ConnectorDto["status"] }), required: true });
      expect(JSON.stringify(state).toLowerCase()).not.toContain("unknown");
    }
  });

  it("relative time reads at a glance", () => {
    expect(relativeTime("2026-10-16T11:59:50Z", NOW)).toBe("just now");
    expect(relativeTime("2026-10-16T11:30:00Z", NOW)).toBe("30 min ago");
    expect(relativeTime("2026-10-16T09:00:00Z", NOW)).toBe("3 h ago");
    expect(relativeTime("2026-10-14T12:00:00Z", NOW)).toBe("2 d ago");
    expect(relativeTime(null, NOW)).toBe("");
  });
});
