// SPDX-License-Identifier: Apache-2.0
import { Alert, Code } from "@/components/ui";
import { shortDate } from "@/lib/format";
import type { Action } from "@/lib/types";

/** One delivery of a write that was sent and never answered, as the effect's journal keeps it. */
interface Sent {
  outpost?: string | null;
  endpoint?: string | null;
  operation?: string | null;
  instance_id?: string | null;
  claimed_at?: string | null;
  arguments_sha256?: string | null;
}

/** What an action in doubt sent and never saw answered: its own summary, else its journal's. */
export function sentInDoubt(action: Action): Sent[] {
  const summary = (action.result as { in_doubt?: { sent?: Sent[] } } | null | undefined)?.in_doubt?.sent;
  if (summary?.length) return summary;
  return (action.journal ?? [])
    .filter((effect) => effect.status === "in_doubt")
    .flatMap((effect) => (effect.result as { in_doubt?: Sent[] } | null | undefined)?.in_doubt ?? []);
}

/**
 * An action in doubt (ADR 0149): a write went through an outpost, an instance took it and its answer
 * never came back. Nobody knows whether it was made, so nothing replays it and nothing is undone. The
 * note says what was sent and what a person does next: settle it (`SettleInDoubt`, S65-26).
 */
export function InDoubt({ action }: { action: Action }) {
  if (action.status !== "in_doubt") return null;
  const sent = sentInDoubt(action);
  return (
    <Alert tone="warn" title="In doubt: this write was sent, and nobody knows whether it was made">
      <div className="space-y-2" data-testid="in-doubt">
        <p>
          An instance of the outpost took the write and its answer never came back. Choregos does not send it again
          and has undone nothing: the effects already done stay done, the ones after it did not run.
        </p>
        {sent.length > 0 && (
          <ul className="space-y-1 text-xs" data-testid="in-doubt-sent">
            {sent.map((delivery, index) => (
              <li key={index} className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                <Code>{delivery.operation ?? "—"}</Code>
                <span>
                  at the outpost <Code>{delivery.outpost ?? "—"}</Code>, endpoint <Code>{delivery.endpoint ?? "—"}</Code>
                </span>
                <span>
                  taken {delivery.claimed_at ? shortDate(delivery.claimed_at) : "at an unknown time"} by the instance{" "}
                  <Code>{delivery.instance_id ?? "—"}</Code>
                </span>
                {delivery.arguments_sha256 && (
                  <span title={delivery.arguments_sha256}>
                    arguments <Code>{delivery.arguments_sha256.slice(0, 12)}</Code>
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
        <p>
          <strong>What to do.</strong> Check the system behind the outpost to see whether the write was made, then
          record what you found below. Made, the action resumes after it; not made, it fails and undoes what it had done
          before. Until then it stays in doubt, and a work item that waits for it waits.
        </p>
      </div>
    </Alert>
  );
}
