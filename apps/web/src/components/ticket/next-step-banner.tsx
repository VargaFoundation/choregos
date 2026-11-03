// SPDX-License-Identifier: Apache-2.0
"use client";

import { CircleCheck, CircleDot, CirclePause, Hand, Loader, OctagonAlert, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { relative } from "@/lib/format";
import type { NextStep, NextStepTone } from "@/lib/next-step";

const FRAME: Record<NextStepTone, string> = {
  info: "border-info-line bg-info-soft",
  warn: "border-waiting-line bg-waiting-soft",
  danger: "border-failed-line bg-failed-soft",
  ok: "border-succeeded-line bg-succeeded-soft",
  neutral: "border-neutral-line bg-neutral-soft",
};

const INK: Record<NextStepTone, string> = {
  info: "text-info",
  warn: "text-waiting-ink",
  danger: "text-failed-ink",
  ok: "text-succeeded-ink",
  neutral: "text-neutral-ink",
};

const ICON: Record<NextStepTone, LucideIcon> = {
  info: Loader,
  warn: Hand,
  danger: OctagonAlert,
  ok: CircleCheck,
  neutral: CirclePause,
};

/**
 * The first thing a ticket says (S55-05): what it waits for and the gesture that moves it on, in the
 * colour of what it means — your turn (amber), an error (red), work in progress (blue), done (green).
 * The words say it too: the colour never speaks alone.
 */
export function NextStepBanner({ step, action }: { step: NextStep; action?: ReactNode }) {
  const Icon = ICON[step.tone] ?? CircleDot;
  return (
    <section
      aria-label="Next step"
      data-testid="next-step"
      data-tone={step.tone}
      className={cn("flex flex-wrap items-center gap-x-4 gap-y-3 rounded-md border px-4 py-3", FRAME[step.tone])}
    >
      <Icon aria-hidden className={cn("size-6 shrink-0", INK[step.tone])} strokeWidth={1.75} />
      <div className="min-w-0 flex-1">
        <p className={cn("text-base font-semibold", INK[step.tone])}>{step.title}</p>
        {(step.detail || step.dueAt) && (
          <p className="text-sm text-ink">
            {step.detail}
            {step.dueAt && <span className="text-ink-muted"> Due {relative(step.dueAt)}.</span>}
          </p>
        )}
      </div>
      {action}
    </section>
  );
}
