// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import { EgressCancelled, EgressRequested, egressRequired } from "@/lib/egress";
import { useSession } from "@/lib/session";
import type { EgressVerdict } from "@/lib/types";
import { EgressModal } from "./egress-modal";

export interface EgressGesture {
  /** What the person is doing, in the imperative: "Install the Notion MCP server". */
  gesture?: string;
  /** Where the request comes from, so the inbox links back: `org-connector:vendor`, `skill-import:<repo>`. */
  origin: string;
  /** Prefilled reason of the request, and of the rule an administrator opens. */
  reason: string;
}

/**
 * Runs a governed "add" with the organisation's network access in mind. `call(false)` first: when
 * the API answers 409 `egress_required`, the modal opens; an administrator who allows retries with
 * `call(true)`, anyone else files a request and the add is abandoned (`EgressRequested`).
 */
export type EgressGuard = <T>(call: (allowEgress: boolean) => Promise<T>, gesture: EgressGesture) => Promise<T>;

/** Without a provider (a component rendered alone) the add just runs: a 409 is then an ordinary error. */
const PASS_THROUGH: EgressGuard = (call) => call(false);

const Context = createContext<EgressGuard>(PASS_THROUGH);

export function useEgressGuard(): EgressGuard {
  return useContext(Context);
}

/** `{ allow_egress: true }` once the modal was answered, nothing before: bodies stay as they were. */
export function egressFlag(allow: boolean): { allow_egress?: true } {
  return allow ? { allow_egress: true } : {};
}

interface Pending extends EgressGesture {
  verdicts: EgressVerdict[];
  resolve: () => void;
  reject: (error: Error) => void;
}

export function EgressProvider({ children }: { children: ReactNode }) {
  const { org } = useSession();
  const queryClient = useQueryClient();
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ask = useCallback(
    (verdicts: EgressVerdict[], gesture: EgressGesture) =>
      new Promise<void>((resolve, reject) => {
        setError(null);
        setPending({ ...gesture, verdicts, resolve, reject });
      }),
    [],
  );

  const guard = useCallback<EgressGuard>(
    async (call, gesture) => {
      try {
        return await call(false);
      } catch (cause) {
        const verdicts = egressRequired(cause);
        if (!verdicts) throw cause;
        await ask(verdicts, gesture);
        return call(true);
      }
    },
    [ask],
  );

  const close = (outcome: () => Error | null) => {
    const current = pending;
    setPending(null);
    setBusy(false);
    const failure = outcome();
    if (failure) current?.reject(failure);
    else current?.resolve();
  };

  async function request() {
    if (!pending) return;
    setBusy(true);
    setError(null);
    try {
      const seen = new Set<string>();
      for (const v of pending.verdicts) {
        const key = `${v.host}:${v.port}`;
        if (v.org !== "absent" || seen.has(key)) continue;
        seen.add(key);
        const same = pending.verdicts.filter((o) => o.host === v.host && o.port === v.port);
        await api.requestOrgEgress(org, {
          host: v.host,
          ports: [v.port],
          used_by: same.map((o) => o.used_by),
          reason: pending.reason,
          origin: pending.origin,
        });
      }
      // The inbox of those who decide, and its counter, read the list: it changed.
      await queryClient.invalidateQueries({ queryKey: ["egress", org] });
      close(() => new EgressRequested([...new Set(pending.verdicts.map((v) => v.host))]));
    } catch (cause) {
      setBusy(false);
      setError(cause instanceof Error ? cause.message : "the request was refused");
    }
  }

  const value = useMemo(() => guard, [guard]);
  return (
    <Context.Provider value={value}>
      {children}
      <EgressModal
        open={pending !== null}
        verdicts={pending?.verdicts ?? []}
        gesture={pending?.gesture}
        busy={busy}
        error={error}
        onAllow={() => close(() => null)}
        onRequest={() => void request()}
        onClose={() => close(() => new EgressCancelled())}
      />
    </Context.Provider>
  );
}
