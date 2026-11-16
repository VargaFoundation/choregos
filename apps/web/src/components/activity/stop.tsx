// SPDX-License-Identifier: Apache-2.0
"use client";

import type { ReactNode } from "react";
import { ConfirmedGesture } from "@/components/confirmed-gesture";
import { REASON_MAX } from "@/lib/activity";

/**
 * A stop that asks why (ADR 0142 §2-5): the button opens a confirmation that says what the stop does,
 * and that cannot run without a reason (1 to 500 characters), which the API audits. A refused stop
 * (403, 409) keeps the confirmation open with the server's own message.
 */
export function StopWithReason({
  question,
  confirm,
  action,
  onDone,
  disabled,
  children = "Stop",
}: {
  question: ReactNode;
  confirm: string;
  action: (reason: string) => Promise<unknown>;
  onDone?: () => void;
  disabled?: boolean;
  children?: ReactNode;
}) {
  return (
    <ConfirmedGesture
      tonDuBouton="danger"
      question={question}
      confirmer={confirm}
      reason={{ label: "Why it is stopped", required: true, maxLength: REASON_MAX }}
      action={action}
      onDone={onDone}
      disabled={disabled}
    >
      {children}
    </ConfirmedGesture>
  );
}
