import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ContextReductionCard } from "@/components/context-reduction";
import { contextReductionOf } from "@/lib/context-reduction";
import type { RunEventDto } from "@/lib/types";

const event = (seq: number, type: string, payload: Record<string, unknown>): RunEventDto => ({
  seq,
  type,
  ts: "2026-10-13T10:00:00Z",
  payload,
});
const given = (context: Record<string, unknown>) =>
  event(1, "context.reduction", {
    shell_filter: "none",
    compression: "none",
    headroom_profile: "coding",
    on_unavailable: "bypass",
    rtk_exclude_commands: [],
    ...context,
  });
const savings = event(9, "context.savings", {
  compression: "headroom",
  provenance: "inferred",
  requests: 12,
  requests_compressed: 9,
  tokens_before: 40_000,
  tokens_removed: 10_000,
  retrievals: 2,
});

const show = (events: RunEventDto[]) => render(<ContextReductionCard reduction={contextReductionOf(events)} />);

describe("the context reduction a run ran with (ADR 0055, S37-08)", () => {
  it("shows nothing for a run given no reduction", () => {
    expect(contextReductionOf([event(1, "run.started", {})])).toBeNull();
    const { container } = show([]);
    expect(container).toBeEmptyDOMElement();
  });

  it("says headroom ran, with its version and profile, and its savings as inferred", () => {
    show([
      given({ compression: "headroom", headroom_profile: "balanced" }),
      event(2, "context.compression", { compression: "headroom", profile: "balanced", version: "0.39.1" }),
      savings,
    ]);
    expect(screen.getByTestId("compression")).toHaveTextContent("ran (0.39.1), profile balanced");
    const saved = screen.getByTestId("context-savings");
    expect(saved).toHaveTextContent("10 k tokens removed of 40 k (25 %)");
    expect(saved).toHaveTextContent("9 of 12 model requests compressed · 2 original(s) asked back");
    // The savings are headroom's count, never shown as observed: the cost is the gateway's.
    expect(saved.querySelector("[data-provenance]")).toHaveAttribute("data-provenance", "inferred");
    expect(saved.querySelector('[data-provenance="observed"]')).toBeNull();
  });

  it("says when headroom was unavailable, and whether the stage went on or failed", () => {
    show([
      given({ compression: "headroom" }),
      event(2, "context.degraded", { compression: "headroom", reason: "did not answer within 60 s", on_unavailable: "bypass" }),
    ]);
    expect(screen.getByTestId("compression")).toHaveTextContent("unavailable, went to the gateway directly");
    expect(screen.getByTestId("compression")).toHaveTextContent("did not answer within 60 s");
    expect(screen.queryByTestId("context-savings")).toBeNull();
  });

  it("says a failed stage failed", () => {
    show([
      given({ compression: "headroom", on_unavailable: "fail" }),
      event(2, "context.degraded", { compression: "headroom", reason: "no gateway key", on_unavailable: "fail" }),
    ]);
    expect(screen.getByTestId("compression")).toHaveTextContent("unavailable, stage failed");
  });

  it("says when rtk was asked of a backend that does not wire it", () => {
    show([
      given({ shell_filter: "rtk" }),
      event(2, "context.shell_filter", { shell_filter: "rtk", backend: "codex", applied: false }),
    ]);
    expect(screen.getByTestId("shell-filter")).toHaveTextContent("not applied: codex has no hook for it");
    expect(screen.queryByTestId("compression")).toBeNull();
  });

  it("says rtk was applied, and never claims a reduction the run did not reach", () => {
    show([given({ shell_filter: "rtk" }), event(2, "context.shell_filter", { shell_filter: "rtk", backend: "claude-code", applied: true })]);
    expect(screen.getByTestId("shell-filter")).toHaveTextContent("applied by claude-code");

    const ended = contextReductionOf([given({ shell_filter: "rtk", compression: "headroom" })])!;
    expect(ended.shellFilter?.applied).toBeNull();
    expect(ended.compression).toEqual({ state: "not started" });
  });
});
