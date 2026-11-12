// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { StopWithReason } from "@/components/activity/stop";
import { TokensAndCostPanel } from "@/components/spend/tokens-panel";
import { Button, Card, Empty, ErrorNote } from "@/components/ui";
import { installationId } from "@/lib/assistants";
import { assistantsApi, type Assistant, type AssistantStopResult } from "@/lib/assistants-api";
import { shortDate } from "@/lib/format";

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/**
 * Stop the assistant, or resume it (S64-09d, ADR 0142 §4 bis). The stop asks why; the installation then
 * reads `suspended` whatever its readiness says, no new turn starts, and every turn in flight of its
 * conversations is cancelled, the conversations staying open to their people. The answer's count is
 * shown, with what could not be stopped now. While it is stopped, the page says who stopped it, when and
 * why, and offers Resume, which restarts nothing on its own. Both need `assistant:manage`, the right
 * this page is read with (`getAssistant`): a refusal is shown where the gesture was made.
 */
export function InstallationStop({ org, assistant }: { org: string; assistant: Assistant }) {
  const client = useQueryClient();
  const [result, setResult] = useState<AssistantStopResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [resuming, setResuming] = useState(false);
  const refresh = () =>
    Promise.all([
      client.invalidateQueries({ queryKey: ["installation", org, assistant.slug] }),
      client.invalidateQueries({ queryKey: ["installations", org] }),
    ]);
  const stopped = assistant.stopped ?? null;

  async function resume() {
    setError(null);
    setResuming(true);
    try {
      const answer = await assistantsApi.resume(org, assistant.slug);
      setResult(null);
      client.setQueryData(["installation", org, assistant.slug], answer);
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Resume refused");
    } finally {
      setResuming(false);
    }
  }

  return (
    <Card title={stopped ? "Stopped" : "Stop the assistant"} data-testid="assistant-stop">
      <div className="space-y-3 text-sm">
        {stopped ? (
          <>
            <p role="status" data-testid="assistant-stopped">
              Stopped by <span data-verbatim>{stopped.by}</span> on {shortDate(stopped.at)}: <span data-verbatim>{stopped.reason}</span>
            </p>
            <p className="text-ink-muted">
              No new turn starts while it is stopped. Its conversations stay open to their people, who read them and
              continue them once it is resumed. Resuming restarts nothing on its own.
            </p>
            {error && <ErrorNote>{error}</ErrorNote>}
            <Button tone="primary" size="sm" disabled={resuming} onClick={() => void resume()}>
              Resume
            </Button>
          </>
        ) : (
          <>
            <p className="text-ink-muted">
              Stopping it suspends it, so that no new turn starts, and cancels the turns its conversations are taking now.
              The conversations stay open to their people.
            </p>
            <StopWithReason
              question={
                <>
                  Stop this assistant? It is <strong>suspended</strong> until it is resumed, whatever its readiness says,
                  and every turn in flight of its conversations is cancelled at its next model or tool call, its spend
                  booked. The conversations are not closed. Resuming restarts nothing on its own.
                </>
              }
              confirm="Stop the assistant"
              action={async (reason) => {
                const answer = await assistantsApi.stop(org, assistant.slug, reason);
                setResult(answer);
                await refresh();
              }}
            >
              Stop the assistant…
            </StopWithReason>
          </>
        )}
        {result && (
          <div role="status" data-testid="assistant-stop-result">
            <p>Stopped. {plural(result.turns_cancelled, "conversation turn cancelled", "conversation turns cancelled")}.</p>
            {(result.not_stopped ?? []).length > 0 && (
              <>
                <p className="text-warn">Not stopped now (the turn&apos;s own lease ends it at the latest):</p>
                <ul className="list-disc pl-5 text-xs text-ink-muted">
                  {(result.not_stopped ?? []).map((item) => (
                    <li key={`${item.kind}-${item.id}`}>
                      <span data-verbatim>
                        {item.kind} {item.id}
                      </span>
                      : {item.detail}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        )}
      </div>
    </Card>
  );
}

/**
 * What the assistant spent (S64-09d, ADR 0141 §1): the organisation's cost report narrowed to this
 * installation, its conversations' turns and its tasks, by model or by agent, in money or in tokens;
 * and its turns on the Activity screen. Narrowing needs the installation's id: an answer that does not
 * carry it is said, never replaced by the organisation's whole spend.
 */
export function InstallationSpend({ assistant }: { assistant: Assistant }) {
  const id = installationId(assistant);
  if (!id) {
    return (
      <Card title="Tokens and cost" data-testid="tokens-and-cost-unavailable">
        <Empty>This installation&apos;s spend cannot be narrowed yet: the answer does not carry its id.</Empty>
      </Card>
    );
  }
  return (
    <div className="space-y-2">
      <TokensAndCostPanel filter={{ assistant: id }} groups={["model", "agent"]} />
      <p className="text-sm">
        <Link href={`/activity?assistant=${encodeURIComponent(id)}`} className="text-ink-muted hover:text-ink hover:underline">
          Its conversation turns on the Activity screen
        </Link>
      </p>
    </div>
  );
}
