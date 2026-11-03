// SPDX-License-Identifier: Apache-2.0
import { Diamond } from "lucide-react";
import { DECISION_HUE } from "@/components/workflows/map/layout";
import { CONFIDENCE_SUFFIX, type WorkflowDecisionActor, type WorkflowDecisionQuestion } from "@/components/workflows/document";
import { cn } from "@/lib/cn";
import { HUE_CLASSES } from "@/lib/workflow-hue";

/**
 * A decision step's mark (ADR 0121): a diamond, the shape a flowchart gives a question, in the teal no
 * other kind of actor wears (ADR 0120). The word goes with it: colour never speaks alone.
 */
export function DecisionChip({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs leading-4 font-medium",
        HUE_CLASSES[DECISION_HUE].pill,
        className,
      )}
    >
      <Diamond aria-hidden className="size-3" strokeWidth={2} />
      Decision
    </span>
  );
}

/** What a question accepts as its answer, in words: what a `when` may compare it with. */
export function answerWords(question: WorkflowDecisionQuestion): string {
  if (question.type === "choice") return `one of ${question.options.join(", ")}`;
  if (question.type === "score") return `one of ${question.levels.join(", ")}, lowest first`;
  return "true or false";
}

/** A question's type, as a reader names it. */
export function questionKind(question: WorkflowDecisionQuestion): string {
  if (question.type === "choice") return "choice";
  if (question.type === "score") return "score";
  return "yes/no";
}

/** A `when` that branches on the first question, written as the workflow would: what to copy, then adapt. */
export function exampleWhen(actor: Pick<WorkflowDecisionActor, "questions">): string | null {
  const first = Object.entries(actor.questions ?? {})[0];
  if (!first) return null;
  const [name, question] = first;
  const answer =
    question.type === "choice" ? question.options[0] : question.type === "score" ? question.levels.at(-1) : "true";
  return `when: { ${name}: ${answer ?? "…"}, ${name}${CONFIDENCE_SUFFIX}: { gte: 0.85 } }`;
}
