// SPDX-License-Identifier: Apache-2.0
"use client";

import Link from "next/link";
import { useState } from "react";
import { Provenance } from "@/components/provenance";
import { callDuration } from "@/components/run-activity";
import { Badge, Button, Card, Disclosure, Table, TBody, TD, TH, THead, TR, type Tone } from "@/components/ui";
import { humanSize } from "@/lib/artifacts";
import { type CallOutcome, outcomeOf, type RunScript, type RunScripts, type ScriptCall, shortDigest } from "@/lib/run-scripts";

const TONES: Record<CallOutcome, Tone> = {
  done: "succeeded",
  proposed: "waiting",
  cap: "failed",
  withheld: "failed",
  timeout: "failed",
  unreachable: "failed",
  failed: "failed",
  unknown: "neutral",
};

const LABELS: Record<CallOutcome, string> = {
  done: "Done",
  proposed: "Proposed — not executed",
  cap: "Cap reached",
  withheld: "Withheld",
  timeout: "Timed out",
  unreachable: "API unreachable",
  failed: "Failed",
  unknown: "Unknown",
};

const MEANINGS: Record<CallOutcome, string> = {
  done: "the platform answered with the tool's result",
  proposed:
    "the platform answered 202: the write became a governed action, which a person decides or which was still running — nothing was done when the script got its answer",
  cap: "the run's tool-call cap was reached: the platform refused the call",
  withheld: "the result was withheld as a suspected injection",
  timeout: "the bridge gave up waiting for the platform; the call may still have completed",
  unreachable: "the bridge could not reach the platform",
  failed: "the platform or the tool refused the call",
  unknown: "the journal line has no status",
};

const HEURE = new Intl.DateTimeFormat("en-GB", { timeStyle: "medium" });

/**
 * The scripts a run's agent wrote to call its tools (ADR 0122 §4, S57-10): each script with its
 * digest, its text and the calls it made, in order. What the bridge relayed is the runner's own
 * record; the calls the model made itself stay in the activity above.
 */
export function RunScriptsCard({ scripts, slug }: { scripts: RunScripts | null; slug: string }) {
  if (!scripts) return null;
  const calls = scripts.scripts.flatMap((script) => script.calls);
  const proposed = calls.filter((call) => outcomeOf(call.statusCode) === "proposed");
  const failed = calls.filter((call) => TONES[outcomeOf(call.statusCode)] === "failed").length;
  return (
    <Card title="Tool scripts" action={<Provenance de="observed" />}>
      <div className="space-y-4" data-testid="run-scripts">
        <p className="font-mono text-xs text-ink-muted" data-testid="scripts-summary">
          {scripts.scripts.length} script{scripts.scripts.length === 1 ? "" : "s"} · {calls.length} call
          {calls.length === 1 ? "" : "s"} through the tool bridge
          {proposed.length > 0 ? ` · ${proposed.length} proposed, not executed` : ""}
          {failed > 0 ? ` · ${failed} refused or failed` : ""}
        </p>
        <ClientLine scripts={scripts} />
        {scripts.scripts.map((script) => (
          <ScriptBlock key={script.sha256 ?? "stdin"} script={script} slug={slug} />
        ))}
        {proposed.some((call) => call.action === null) && (
          <p className="text-xs text-ink-muted" data-testid="proposal-not-linked">
            The run&apos;s journal does not record which action a script&apos;s proposal created. Find it among the{" "}
            <Link href={`/p/${encodeURIComponent(slug)}/actions`} className="underline">
              project&apos;s actions
            </Link>
            , where a person approves or rejects it.
          </p>
        )}
      </div>
    </Card>
  );
}

function ClientLine({ scripts }: { scripts: RunScripts }) {
  const client = scripts.client;
  if (!client) return null;
  if (!client.written)
    return (
      <p className="text-sm" data-testid="tools-client">
        <Badge tone="warn">Client not written</Badge>{" "}
        <span className="text-ink-muted">The run was offered tool scripts, but its tool client could not be written</span>
        {client.error && <span className="block font-mono text-xs text-ink-muted">{client.error}</span>}
      </p>
    );
  return (
    <p className="text-sm text-ink-muted" data-testid="tools-client">
      The run was offered tool scripts: its client lists {client.tools ?? 0} tool{client.tools === 1 ? "" : "s"}
      {client.modules.length > 0 ? ` in ${client.modules.length} module${client.modules.length === 1 ? "" : "s"}` : ""}.
      {scripts.scripts.length === 0 && " The agent ran no script."}
      {client.error && <span className="block font-mono text-xs">{client.error}</span>}
    </p>
  );
}

function ScriptBlock({ script, slug }: { script: RunScript; slug: string }) {
  const label = script.sha256 ? `Script ${shortDigest(script.sha256)}` : "Scripts piped on standard input";
  return (
    <section
      aria-label={label}
      className="space-y-2 border-t border-line pt-3 first-of-type:border-t-0 first-of-type:pt-0"
      data-script={script.sha256 ?? "stdin"}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold text-ink">
          {script.sha256 ? (
            <>
              Script <span className="font-mono" title={`SHA-256 ${script.sha256}`}>{shortDigest(script.sha256)}</span>
            </>
          ) : (
            label
          )}
        </h3>
        <span className="font-mono text-xs text-ink-muted">
          {script.calls.length} call{script.calls.length === 1 ? "" : "s"}
          {script.bytes !== null ? ` · ${humanSize(script.bytes)}` : ""}
        </span>
      </div>
      {script.text !== null ? (
        <ScriptText text={script.text} digest={script.sha256} />
      ) : (
        <p className="text-xs text-ink-muted" data-testid="script-text-absent">
          Text not recorded:{" "}
          {script.sha256
            ? "the script's text never reached the bridge, only its digest did."
            : "a script piped on standard input has no file, so its calls carry no digest and its text is not sent. Calls of several such scripts cannot be told apart."}
        </p>
      )}
      {script.calls.length > 0 && <ScriptCalls calls={script.calls} slug={slug} label={label} />}
    </section>
  );
}

function ScriptText({ text, digest }: { text: string; digest: string | null }) {
  const [copied, setCopied] = useState(false);
  const lines = text.split("\n").length;
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }
  return (
    <Disclosure title="Script text" summary={`${lines} line${lines === 1 ? "" : "s"}`}>
      <div className="flex items-center justify-end gap-2">
        {copied && (
          <span className="text-xs text-ink-muted" role="status">
            Copied
          </span>
        )}
        <Button size="sm" aria-label={`Copy the text of the script${digest ? ` ${shortDigest(digest)}` : ""}`} onClick={() => void copy()}>
          Copy
        </Button>
      </div>
      <pre className="max-h-96 overflow-auto rounded-sm bg-surface-sunken p-3 font-mono text-xs" data-verbatim data-testid="script-text">
        {text}
      </pre>
    </Disclosure>
  );
}

function ScriptCalls({ calls, slug, label }: { calls: ScriptCall[]; slug: string; label: string }) {
  return (
    <div className="overflow-x-auto">
      <Table aria-label={`Calls of ${label}`}>
        <THead>
          <TR>
            <TH>Started</TH>
            <TH>Tool</TH>
            <TH>Status</TH>
            <TH align="right">Duration</TH>
            <TH align="right">Answer</TH>
          </TR>
        </THead>
        <TBody>
          {calls.map((call) => {
            const outcome = outcomeOf(call.statusCode);
            return (
              <TR key={call.seq} data-call-seq={call.seq} data-outcome={outcome}>
                <TD className="font-mono text-xs whitespace-nowrap text-ink-muted tabular-nums">{HEURE.format(new Date(call.ts))}</TD>
                <TD>
                  <span className="font-mono text-xs break-all">{call.tool}</span>
                </TD>
                <TD>
                  <span className="flex flex-wrap items-center gap-2" title={MEANINGS[outcome]}>
                    <Badge tone={TONES[outcome]}>
                      {LABELS[outcome]}
                      {call.statusCode !== null ? ` · ${call.statusCode}` : ""}
                    </Badge>
                    {outcome === "proposed" && call.action && (
                      <Link href={`/p/${encodeURIComponent(slug)}/actions/${call.action}`} className="text-xs underline">
                        Open the proposal
                      </Link>
                    )}
                  </span>
                </TD>
                <TD align="right" className="font-mono text-xs whitespace-nowrap tabular-nums">
                  {callDuration(call.durationMs ?? undefined)}
                </TD>
                <TD align="right" className="font-mono text-xs whitespace-nowrap tabular-nums">
                  {call.responseBytes !== null ? humanSize(call.responseBytes) : "—"}
                </TD>
              </TR>
            );
          })}
        </TBody>
      </Table>
    </div>
  );
}
