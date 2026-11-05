// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { DEFAULT_TARGET_ACCURACY, DecisionCalibration, type ThresholdWording } from "@/components/workflows/decision-calibration";
import { api } from "@/lib/api";

/** The routing's calibration from the ledger (ADR 0123 §4): absent while it loads, its error when the API refuses. */
export function useRoutingCalibration(project: string) {
  return useQuery({
    queryKey: ["routing-calibration", project],
    queryFn: () => api.routingCalibration(project, DEFAULT_TARGET_ACCURACY),
    retry: false,
    staleTime: 60_000,
  });
}

/** The routing's wording: its threshold is the project's minimum confidence, not a workflow's `when`. */
export const ROUTING_WORDING: ThresholdWording = {
  lets: (_output, value) => <>The routing takes the model&apos;s answer from a probability ≥ {value}, the minimum confidence</>,
  none: () => <>No minimum confidence is set: the routing takes every answer the model gives.</>,
  named: "The minimum confidence",
  empty:
    "No routing recorded yet: the ledger measures it once a decision model has routed a work item and a person changed its workflow, or it ended. Until then the minimum confidence is a guess.",
};

/**
 * How well the decision model that routes the project's work items chose their workflow (ADR 0123
 * §4, S52-12): the step calibration's card over the routing's records — a routing is right when no
 * one changed the work item's workflow, or when the workflow a person moved it to is the model's
 * answer — with the project's minimum confidence where a step reads its workflow's threshold.
 */
export function RoutingCalibrationCard({ slug }: { slug: string }) {
  const read = useRoutingCalibration(slug);
  const minimum = read.data?.min_confidence;
  const thresholds =
    typeof minimum === "number"
      ? Object.fromEntries((read.data?.questions ?? []).map((question) => [question.question, minimum]))
      : undefined;
  return (
    <div className="space-y-2 text-sm">
      <p className="text-ink-muted">
        A routing is right when nobody changed the ticket&apos;s workflow before it ended, or when the workflow a person moved
        it to is the one the model answered. A ticket cancelled without a change is not counted.
      </p>
      <DecisionCalibration read={read} thresholds={thresholds} wording={ROUTING_WORDING} />
    </div>
  );
}
