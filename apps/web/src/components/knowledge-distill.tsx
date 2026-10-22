// SPDX-License-Identifier: Apache-2.0
"use client";

import { useState } from "react";
import { Card, ErrorNote, buttonClasses } from "@/components/ui";
import { api } from "@/lib/api";
import type { KnowledgeDistillEstimate } from "@/lib/types";

/** What the person confirms is the estimate plus this margin: a run that costs more is stopped. */
export const CAP_MARGIN = 1.25;

function money(value: number | null | undefined): string {
  return value == null ? "not priced" : `$${value.toFixed(2)}`;
}

function cost(estimate: KnowledgeDistillEstimate): number | null {
  if (estimate.input_cost_usd == null || estimate.output_cost_usd == null) return null;
  return estimate.input_cost_usd + estimate.output_cost_usd;
}

/** The cap sent with the start: the estimate plus 25 %, or none when nothing is priced. */
export function capFor(estimate: KnowledgeDistillEstimate): number | undefined {
  const total = cost(estimate);
  return total == null ? undefined : Math.ceil(total * CAP_MARGIN * 100) / 100;
}

/**
 * "Distill" asks what the run would read and cost before starting it (S42-10c): characters, tokens,
 * documents, parts, calls, the model, the price, and the biggest documents, each of which can be left
 * out (the estimate is asked again). Confirming sends the exclusions and a cap of estimate + 25 %.
 */
export function KnowledgeDistill({
  org,
  slug,
  onStarted,
}: {
  org: string;
  slug: string;
  onStarted: () => Promise<unknown>;
}) {
  const [estimate, setEstimate] = useState<KnowledgeDistillEstimate | null>(null);
  const [excluded, setExcluded] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function ask(next: string[]) {
    setBusy(true);
    setError(null);
    try {
      setEstimate(await api.estimateKnowledgeDistillation(org, slug, next));
      setExcluded(next);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Refused");
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (!estimate) return;
    setBusy(true);
    setError(null);
    try {
      const cap = capFor(estimate);
      await api.distillKnowledgeBase(org, slug, {
        ...(excluded.length ? { exclude: excluded } : {}),
        ...(cap !== undefined ? { max_usd: cap } : {}),
      });
      setEstimate(null);
      setExcluded([]);
      await onStarted();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Refused");
    } finally {
      setBusy(false);
    }
  }

  function toggle(filename: string) {
    void ask(excluded.includes(filename) ? excluded.filter((e) => e !== filename) : [...excluded, filename]);
  }

  return (
    <>
      <button type="button" className="ml-2 underline" disabled={busy} onClick={() => void ask([])}>
        Distill
      </button>
      {error && <ErrorNote>{error}</ErrorNote>}
      {estimate && (
        <Card title="Distillation estimate">
          <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-3">
            <dt className="text-ink-muted">Reads</dt>
            <dd data-testid="estimate-reads">
              {estimate.characters.toLocaleString("en-US")} characters (≈ {estimate.tokens_estimate.toLocaleString("en-US")} tokens)
            </dd>
            <dt className="text-ink-muted">Documents</dt>
            <dd>{estimate.documents}</dd>
            <dt className="text-ink-muted">Parts and calls</dt>
            <dd data-testid="estimate-calls">
              {estimate.parts} {estimate.parts === 1 ? "part" : "parts"}, {estimate.calls} model {estimate.calls === 1 ? "call" : "calls"}
            </dd>
            <dt className="text-ink-muted">Model</dt>
            <dd className="font-mono">{estimate.model}</dd>
            <dt className="text-ink-muted">Estimated cost</dt>
            <dd data-testid="estimate-cost">{money(cost(estimate))}</dd>
          </dl>
          <p className="mt-2 text-xs text-ink-muted">
            The run stops, and publishes nothing, if it would spend more than{" "}
            {capFor(estimate) === undefined ? `the platform budget of ${money(estimate.budget_usd)}` : money(capFor(estimate))}.
          </p>
          {estimate.biggest.length > 0 && (
            <ul className="mt-3 space-y-1 text-sm" data-testid="estimate-biggest">
              {estimate.biggest.map((one) => (
                <li key={one.filename} className="flex items-center gap-2">
                  <label className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={excluded.includes(one.filename)}
                      disabled={busy}
                      onChange={() => toggle(one.filename)}
                    />
                    <span>
                      Leave out <span className="font-mono">{one.filename}</span>
                    </span>
                  </label>
                  <span className="text-xs text-ink-muted">{one.characters.toLocaleString("en-US")} characters</span>
                </li>
              ))}
            </ul>
          )}
          {estimate.refused_reason && (
            <p role="alert" className="mt-3 text-sm text-danger" data-testid="estimate-refused">
              {estimate.refused_reason}
            </p>
          )}
          <div className="mt-3 flex gap-2">
            <button
              type="button"
              className={buttonClasses("primary")}
              disabled={busy || !!estimate.refused_reason}
              onClick={() => void confirm()}
            >
              Start distillation
            </button>
            <button type="button" className={buttonClasses("secondary")} onClick={() => setEstimate(null)}>
              Cancel
            </button>
          </div>
        </Card>
      )}
    </>
  );
}
