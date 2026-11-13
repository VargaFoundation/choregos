// SPDX-License-Identifier: Apache-2.0
"use client";

import { Badge, Button, EngineSentence, ErrorNote } from "@/components/ui";
import { hasKeyArgument, type DeliveryDraft, type DeliveryStatement } from "@/lib/outposts";
import type { ConnectorOperation } from "@/lib/types";

/** What the API said when it refused a change of the rule: its sentence, and the statements it named. */
export interface StatementRefusal {
  detail: string | null;
  fields: Partial<Record<DeliveryStatement, string>>;
}

/**
 * How a write through an outpost is delivered (ADR 0149 §5-6), in the operation's rule. Such a write is
 * delivered at most once: when the instance that claimed it never answers, nobody knows whether the
 * outpost's server made it. It is opened (`allowed` or `approval`) only when its rule says one of two
 * things, and silence opens nothing: the operation takes the platform's idempotency key
 * (`platform_arguments: ["action_key"]`), or an in-doubt outcome is accepted for it, explicitly
 * (`in_doubt_accepted`). What is ticked here leaves with the next policy that opens the write, or alone
 * with "Set". The API judges the rule as the change would leave it, and its refusals are shown as it gives
 * them: its sentence, and its words under each statement it names.
 */
export function OperationStatements({
  id,
  operation,
  draft,
  stored,
  refused,
  onChange,
  onSave,
}: {
  id: string;
  operation: ConnectorOperation;
  draft: DeliveryDraft;
  stored: DeliveryDraft;
  refused: StatementRefusal | null;
  onChange: (draft: DeliveryDraft) => void;
  onSave: () => void;
}) {
  // A key the server would never read promises nothing: the API refuses it. One already stated can be withdrawn.
  const keyArgument = hasKeyArgument(operation);
  const keyClosed = !keyArgument && !stored.key;
  const changed = draft.key !== stored.key || draft.accepted !== stored.accepted;
  const saysNeither = !stored.key && !stored.accepted;
  return (
    <div className="space-y-2 rounded border border-line p-3 text-sm" data-testid={`delivery-${operation.name}`} role="group" aria-labelledby={`${id}-title`}>
      <div className="flex flex-wrap items-center gap-2">
        <strong id={`${id}-title`}>How {operation.name} is delivered</strong>
        {saysNeither ? (
          <Badge tone="warn" data-testid={`delivery-state-${operation.name}`}>
            Says neither: it stays forbidden
          </Badge>
        ) : (
          <Badge tone="ok" data-testid={`delivery-state-${operation.name}`}>
            {stored.key && stored.accepted ? "Both stated" : stored.key ? "Takes the key" : "In doubt accepted"}
          </Badge>
        )}
      </div>
      <p className="text-xs text-ink-muted">
        A write through an outpost is delivered at most once: when the instance that took it never answers, nobody knows
        whether it was made. It is opened (<code>allowed</code> or <code>approval</code>) only when its rule says one of
        two things; silence opens nothing.
      </p>
      <label className="flex items-start gap-2">
        <input
          type="checkbox"
          className="mt-1"
          checked={draft.key}
          disabled={keyClosed}
          aria-describedby={`${id}-key-help`}
          onChange={(event) => onChange({ ...draft, key: event.target.checked })}
        />
        <span>
          It takes the platform&apos;s idempotency key (<code>action_key</code>)
          <span id={`${id}-key-help`} className="block text-xs text-ink-muted">
            {keyArgument
              ? "The tool has an `action_key` argument and makes a call that arrives twice with the same key harmless. Choregos fills it; after a doubt the write may be delivered again with the same key. Choregos does not verify that the server honours it."
              : "The tool's input schema has no `action_key` argument: the platform cannot hand it one."}
          </span>
          {refused?.fields.platform_arguments && (
            <span className="block text-xs text-danger" data-testid={`refused-platform_arguments-${operation.name}`}>
              <EngineSentence text={refused.fields.platform_arguments} />
            </span>
          )}
        </span>
      </label>
      <label className="flex items-start gap-2">
        <input
          type="checkbox"
          className="mt-1"
          checked={draft.accepted}
          aria-describedby={`${id}-doubt-help`}
          onChange={(event) => onChange({ ...draft, accepted: event.target.checked })}
        />
        <span>
          An in-doubt outcome is accepted for it, explicitly (<code>in_doubt_accepted</code>)
          <span id={`${id}-doubt-help`} className="block text-xs text-ink-muted">
            An action that calls it may end <code>in_doubt</code>: never replayed, never compensated automatically. A person
            checks the system behind the outpost.
          </span>
          {refused?.fields.in_doubt_accepted && (
            <span className="block text-xs text-danger" data-testid={`refused-in_doubt_accepted-${operation.name}`}>
              <EngineSentence text={refused.fields.in_doubt_accepted} />
            </span>
          )}
        </span>
      </label>
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" onClick={onSave} disabled={!changed}>
          Set
        </Button>
        {changed && <span className="text-xs text-ink-muted">Also sent with the next policy that opens it.</span>}
      </div>
      {refused?.detail && (
        <div data-testid={`delivery-refusal-${operation.name}`}>
          <ErrorNote>
            <EngineSentence text={refused.detail} />
          </ErrorNote>
        </div>
      )}
    </div>
  );
}
