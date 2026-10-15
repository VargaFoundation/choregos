// SPDX-License-Identifier: Apache-2.0
/**
 * The context reduction a run ran with (ADR 0055, S37-08), read back from its journal.
 *
 * The runner writes what it was given (`context.reduction`), whether its backend wired the shell
 * filter (`context.shell_filter`), whether headroom ran (`context.compression`) or not
 * (`context.degraded`), and what headroom says it saved (`context.savings`). The first four are
 * the platform's own observations. The savings are headroom's count, before the gateway: inferred
 * (ADR 0045), never the run's cost.
 */
import type { RunEventDto } from "@/lib/types";

export type ShellFilter = {
  filter: string;
  backend: string | null;
  /** `null`: the run ended before its backend was launched. */
  applied: boolean | null;
};

export type Compression =
  | { state: "ran"; version: string | null; profile: string | null }
  | { state: "unavailable"; reason: string; failed: boolean }
  /** Asked, but the run ended before the proxy was started. */
  | { state: "not started" };

export type Savings = {
  requests: number;
  requestsCompressed: number;
  tokensBefore: number;
  tokensRemoved: number;
  retrievals: number;
};

export type ContextReduction = {
  shellFilter: ShellFilter | null;
  compression: Compression | null;
  savings: Savings | null;
};

type Payload = Record<string, unknown>;

const count = (value: unknown): number => (typeof value === "number" && Number.isFinite(value) ? value : 0);
const text = (value: unknown): string | null => (typeof value === "string" && value ? value : null);

/** `null` when the run was given no reduction: nothing to show, nothing to claim. */
export function contextReductionOf(events: RunEventDto[]): ContextReduction | null {
  const last = (type: string): Payload | null => {
    const found = events.filter((event) => event.type === type).at(-1);
    return found ? ((found.payload ?? {}) as Payload) : null;
  };
  const given = last("context.reduction");
  if (!given) return null;

  let shellFilter: ShellFilter | null = null;
  const filter = text(given.shell_filter);
  if (filter && filter !== "none") {
    const wired = last("context.shell_filter");
    shellFilter = {
      filter,
      backend: wired ? text(wired.backend) : null,
      applied: wired ? Boolean(wired.applied) : null,
    };
  }

  let compression: Compression | null = null;
  const compressor = text(given.compression);
  if (compressor && compressor !== "none") {
    const ran = last("context.compression");
    const degraded = last("context.degraded");
    compression = ran
      ? { state: "ran", version: text(ran.version), profile: text(ran.profile) }
      : degraded
        ? { state: "unavailable", reason: text(degraded.reason) ?? "", failed: degraded.on_unavailable === "fail" }
        : { state: "not started" };
  }

  const saved = last("context.savings");
  const savings: Savings | null = saved
    ? {
        requests: count(saved.requests),
        requestsCompressed: count(saved.requests_compressed),
        tokensBefore: count(saved.tokens_before),
        tokensRemoved: count(saved.tokens_removed),
        retrievals: count(saved.retrievals),
      }
    : null;

  return { shellFilter, compression, savings };
}
