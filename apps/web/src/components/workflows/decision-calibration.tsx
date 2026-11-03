// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { EtatDeLecture } from "@/components/ui";
import { DECISION_HUE } from "@/components/workflows/map/layout";
import { CONFIDENCE_SUFFIX, transitions, type WorkflowDocument } from "@/components/workflows/document";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { percent } from "@/lib/format";
import type { QuestionCalibration, StepCalibration } from "@/lib/types";
import { HUE_CLASSES } from "@/lib/workflow-hue";

/** The accuracy a suggested threshold must reach, as the API defaults it (ADR 0121 §7). */
export const DEFAULT_TARGET_ACCURACY = 0.9;

/** Where a decision step's calibration is read: its project, its workflow and the thresholds its `when` sets. */
export type CalibrationSource = {
  project: string;
  workflow: string;
  /** The threshold the workflow's `when` sets on each question's probability, by question. */
  thresholds?: Record<string, number>;
};

/** A threshold, as the workflow writes it. */
const threshold = (value: number) => value.toFixed(2);

/** The lowest probability a condition lets through: its `gte` or `gt`; nothing for an equality or an upper bound. */
function lowerBound(condition: unknown): number | null {
  if (!condition || typeof condition !== "object" || Array.isArray(condition)) return null;
  const { gte, gt } = condition as { gte?: unknown; gt?: unknown };
  const bound = typeof gte === "number" ? gte : typeof gt === "number" ? gt : null;
  return bound !== null && Number.isFinite(bound) ? bound : null;
}

/**
 * The threshold a workflow sets on each question's probability: the `when` of the exits of the state a
 * decision leads to compares `<question>_confidence` with `gte` (or `gt`). Two exits with two
 * thresholds: the lowest, the one that lets the most answers through.
 */
export function workflowThresholds(document: WorkflowDocument | undefined, state: string): Record<string, number> {
  const found: Record<string, number> = {};
  for (const exit of transitions(document)) {
    if (exit.from !== state) continue;
    for (const [output, condition] of Object.entries(exit.when ?? {})) {
      if (!output.endsWith(CONFIDENCE_SUFFIX)) continue;
      const bound = lowerBound(condition);
      if (bound === null) continue;
      const question = output.slice(0, -CONFIDENCE_SUFFIX.length);
      const known = found[question];
      found[question] = known === undefined ? bound : Math.min(known, bound);
    }
  }
  return found;
}

/** A step's calibration from the ledger (S52-05): absent while it loads, its error when the API refuses. */
export function useStepCalibration(project: string, workflow: string, step: string) {
  return useQuery({
    queryKey: ["step-calibration", project, workflow, step],
    queryFn: () => api.stepCalibration(project, workflow, step, DEFAULT_TARGET_ACCURACY),
    retry: false,
    staleTime: 60_000,
  });
}

/** The read of a step's calibration, as `useStepCalibration` returns it. */
export type CalibrationRead = ReturnType<typeof useStepCalibration>;

/** Whether the ledger observed the outcome of at least one of the step's decisions. */
export function isMeasured(calibration: StepCalibration | undefined): boolean {
  return (calibration?.questions ?? []).some((question) => question.observed > 0);
}

export const GUESS_SENTENCE =
  "The probability is the model's own until the platform has measured this step: a threshold on it is a guess.";
export const MEASURED_SENTENCE =
  "The probability is measured against what happened next: how often the answers were right at each threshold is under Calibration.";

/** What a threshold on the probability is worth: a guess, until the ledger has observed an outcome. */
export function ProbabilityNote({ calibration }: { calibration: StepCalibration | undefined }) {
  return <>{isMeasured(calibration) ? MEASURED_SENTENCE : GUESS_SENTENCE}</>;
}

/** The point the API measured at a threshold, or the nearest one below it when the workflow's lies between two. */
function pointAt(question: QuestionCalibration, value: number) {
  const points = [...(question.thresholds ?? [])].sort((a, b) => a.threshold - b.threshold);
  const exact = points.find((point) => Math.abs(point.threshold - value) < 1e-9);
  if (exact) return { point: exact, exact: true };
  const below = points.filter((point) => point.threshold < value).at(-1);
  return below ? { point: below, exact: false } : null;
}

/** Who gave the probability: the decision model and its version, or the agent that declared it. */
function whoAnswered(question: QuestionCalibration): string {
  const version = question.model_version ? ` (${question.model_version})` : "";
  return question.source === "agent" ? `declared by ${question.model}${version}` : `answered by ${question.model}${version}`;
}

/** What the workflow's own threshold keeps, and how it compares with the suggested one. */
function WorkflowThreshold({ question, value }: { question: QuestionCalibration; value: number | undefined }) {
  const output = `${question.question}${CONFIDENCE_SUFFIX}`;
  if (value === undefined)
    return (
      <p className="text-ink-muted">
        The workflow&apos;s <code>when</code> sets no threshold on <code>{output}</code>.
      </p>
    );
  const measured = pointAt(question, value);
  const suggested = typeof question.suggested_threshold === "number" ? question.suggested_threshold : null;
  return (
    <>
      <p data-testid={`workflow-threshold-${question.question}`}>
        The workflow lets an answer through from <code>{output}</code> ≥ {threshold(value)}
        {measured && measured.point.count > 0 ? (
          <>
            {measured.exact ? ": " : ` (measured at ${threshold(measured.point.threshold)}, the nearest threshold below): `}
            {percent(measured.point.coverage)} of the observed decisions reach it ({measured.point.count}), {percent(measured.point.accuracy)}{" "}
            of them right.
          </>
        ) : (
          ": no observed decision reaches it yet."
        )}
      </p>
      {suggested !== null && value < suggested - 1e-9 && (
        <p className="font-medium text-retrying-ink" data-testid={`threshold-below-${question.question}`}>
          The workflow&apos;s threshold, {threshold(value)}, is below the suggested {threshold(suggested)}: answers between the two are
          let through although, measured, they are right less often than the target.
        </p>
      )}
    </>
  );
}

/** Ten bins: what the model said (mean probability) against how often it was right — a table a screen reader reads. */
function Reliability({ question }: { question: QuestionCalibration }) {
  const bins = question.bins ?? [];
  if (bins.length === 0) return null;
  return (
    <div className="overflow-x-auto">
      <table className="text-xs" aria-label={`Reliability of ${question.question}`}>
        <thead>
          <tr>
            <th className="pr-3 text-left font-medium text-ink-muted">Probability</th>
            <th className="pr-3 text-right font-medium text-ink-muted">Decisions</th>
            <th className="pr-3 text-right font-medium text-ink-muted">Said</th>
            <th className="text-left font-medium text-ink-muted">Right</th>
          </tr>
        </thead>
        <tbody>
          {bins.map((bin) => (
            <tr key={bin.lower}>
              <td className="pr-3 tabular-nums">
                {bin.lower.toFixed(1)}–{bin.upper.toFixed(1)}
              </td>
              <td className="pr-3 text-right tabular-nums">{bin.count}</td>
              <td className="pr-3 text-right tabular-nums">{percent(bin.mean_probability)}</td>
              <td className="tabular-nums">
                <span className="flex items-center gap-2">
                  <span className="w-10 text-right">{percent(bin.accuracy)}</span>
                  {bin.count > 0 && (
                    <span aria-hidden className="relative block h-2 w-24 rounded-sm bg-surface-muted">
                      <span
                        className={cn("absolute inset-y-0 left-0 rounded-sm", HUE_CLASSES[DECISION_HUE].solid)}
                        style={{ width: `${Math.round((bin.accuracy ?? 0) * 100)}%` }}
                      />
                      <span
                        className="absolute -inset-y-0.5 w-0.5 bg-ink"
                        style={{ left: `${Math.round((bin.mean_probability ?? 0) * 100)}%` }}
                      />
                    </span>
                  )}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-1 text-ink-muted">
        The bar is how often the answers were right, the line what the model said: a calibrated model&apos;s bar reaches its line.
      </p>
    </div>
  );
}

/** Why no threshold is suggested: too few observed decisions, said with the count, or the API's own reason. */
export function noSuggestionReason(question: QuestionCalibration, minObserved: number): string {
  if (question.observed < minObserved)
    return `${question.observed} observed decision${question.observed === 1 ? "" : "s"}; ${minObserved} are needed`;
  return question.suggestion_reason ?? "no threshold reaches the target accuracy";
}

function QuestionCard({ question, target, minObserved, workflowThreshold }: {
  question: QuestionCalibration;
  target: number;
  minObserved: number;
  workflowThreshold: number | undefined;
}) {
  return (
    <section
      aria-label={`Calibration of ${question.question}, ${whoAnswered(question)}`}
      className="space-y-1 rounded-md border border-line px-3 py-2"
    >
      <p>
        <code className="text-ink">{question.question}</code> <span className="text-ink-muted">— {whoAnswered(question)}</span>
      </p>
      <p data-testid={`calibration-numbers-${question.question}`}>
        {question.observed} of {question.count} decisions observed
        {question.observed > 0 && (
          <>
            {" "}
            · {percent(question.accuracy)} of them right · expected calibration error{" "}
            {question.ece === null || question.ece === undefined ? "—" : question.ece.toFixed(2)}
          </>
        )}
      </p>
      {question.suggested_threshold !== null && question.suggested_threshold !== undefined ? (
        <p data-testid={`suggested-threshold-${question.question}`}>
          Suggested threshold for {percent(target)} accuracy: <strong>{threshold(question.suggested_threshold)}</strong>, the lowest
          that reaches it.
        </p>
      ) : (
        <p className="text-ink-muted" data-testid={`no-suggestion-${question.question}`}>
          No threshold suggested: {noSuggestionReason(question, minObserved)}.
        </p>
      )}
      <WorkflowThreshold question={question} value={workflowThreshold} />
      {question.observed > 0 && <Reliability question={question} />}
    </section>
  );
}

/**
 * A decision step's calibration (ADR 0121 §7, S52-06b): for each question, how many decisions the
 * ledger recorded and observed, how often they were right, the expected calibration error, ten
 * reliability bins, what the workflow's own threshold keeps, and the threshold the ledger suggests
 * for the target accuracy — or why it suggests none. Measured, never declared.
 */
export function DecisionCalibration({ read, thresholds }: { read: CalibrationRead; thresholds?: Record<string, number> }) {
  if (!read.data) return <EtatDeLecture lecture={read} quoi="the calibration" />;
  const { questions = [], target_accuracy: target, min_observed: minObserved } = read.data;
  if (questions.length === 0)
    return (
      <p className="text-ink-muted" data-testid="calibration-empty">
        No decision recorded yet: the ledger measures this step once it has decided and the outcome of a work item is known. Until
        then a threshold on the probability is a guess.
      </p>
    );
  return (
    <div className="space-y-2" data-testid="decision-calibration">
      {questions.map((question) => (
        <QuestionCard
          key={`${question.question}-${question.source}-${question.model}-${question.model_version ?? ""}`}
          question={question}
          target={target}
          minObserved={minObserved}
          workflowThreshold={thresholds?.[question.question]}
        />
      ))}
    </div>
  );
}
