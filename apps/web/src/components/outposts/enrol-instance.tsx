// SPDX-License-Identifier: Apache-2.0
"use client";

import { useState } from "react";
import { Extrait } from "@/components/integrations/panel";
import { Button, Card, Code } from "@/components/ui";
import { api } from "@/lib/api";
import { relative, shortDate } from "@/lib/format";
import { enrolCommand, POLICY_FILE, policyLines, refusalOf, type OutpostRefusal } from "@/lib/outposts";
import type { OutpostEnrolmentCreated } from "@/lib/types";
import { RefusalNote } from "./refusal";

/**
 * Enrolling an instance: a single-use token, shown once, with what the operator of the outpost puts
 * in the policy file (the door's origin and the keys to pin) and the command to run there. The token
 * lives in this card's state only: closing it, or leaving the page, loses it for good, and the
 * platform keeps its digest.
 */
export function EnrolInstance({
  org,
  slug,
  onIssued,
  onDone,
}: {
  org: string;
  slug: string;
  /** A token is on screen: an instance is about to appear. */
  onIssued?: () => void;
  onDone?: () => void;
}) {
  const [issued, setIssued] = useState<OutpostEnrolmentCreated | null>(null);
  const [refusal, setRefusal] = useState<OutpostRefusal | null>(null);
  const [busy, setBusy] = useState(false);

  async function issue() {
    setBusy(true);
    setRefusal(null);
    try {
      setIssued(await api.createOutpostEnrolment(org, slug));
      onIssued?.();
    } catch (cause) {
      setRefusal(refusalOf(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Enrol an instance" data-testid="outpost-enrol">
      <div className="space-y-3 text-sm">
        <p className="text-ink-muted">
          An instance is one copy of the program <Code>choregos-outpost</Code>, with keys it makes on its own machine. It
          enrols with a token that works once and lives 15 minutes, and is handed nothing until you approve it against its
          fingerprint.
        </p>
        {!issued && (
          <Button tone="primary" onClick={() => void issue()} disabled={busy}>
            {busy ? "Creating the token…" : "Create an enrolment token"}
          </Button>
        )}
        {refusal && <RefusalNote refusal={refusal} />}
        {issued && (
          <div className="space-y-3 rounded border border-line border-l-2 border-l-accent bg-surface p-3" data-testid="outpost-enrolment">
            <p className="text-xs text-ink-muted">Copy what follows now: the token will not be shown again.</p>
            <dl className="grid gap-x-4 gap-y-1 sm:grid-cols-[auto_1fr]">
              <dt className="text-ink-muted">Token</dt>
              <dd>
                <code className="block break-all font-mono text-xs" data-testid="outpost-enrolment-token">
                  {issued.token}
                </code>
              </dd>
              <dt className="text-ink-muted">Expires</dt>
              <dd data-testid="outpost-enrolment-expiry">
                {shortDate(issued.expires_at)} <span className="text-xs text-ink-muted">({relative(issued.expires_at)}), and it works once</span>
              </dd>
              <dt className="text-ink-muted">Door&apos;s origin</dt>
              <dd data-testid="outpost-enrolment-origin">
                {issued.control_plane ? (
                  <code className="break-all font-mono text-xs">{issued.control_plane}</code>
                ) : (
                  <span className="text-ink-muted">This deployment declares none: ask its operator.</span>
                )}
              </dd>
              <dt className="text-ink-muted">Keys to pin</dt>
              <dd>
                <ul data-testid="outpost-enrolment-keys">
                  {issued.door_keys.map((key, index) => (
                    <li key={key}>
                      <code className="break-all font-mono text-xs">{key}</code>{" "}
                      <span className="text-xs text-ink-muted">{index === 0 ? "signs now" : "announced for a rotation"}</span>
                    </li>
                  ))}
                </ul>
              </dd>
            </dl>
            <div className="space-y-1">
              <p>
                1. In the outpost&apos;s policy file (<Code>{POLICY_FILE}</Code> by default), before the program starts:
              </p>
              <Extrait code={policyLines(issued.control_plane, issued.door_keys)} testId="outpost-policy-lines" />
            </div>
            <div className="space-y-1">
              <p>2. On the outpost&apos;s machine, before the token expires:</p>
              <Extrait code={enrolCommand(issued.token)} testId="outpost-enrol-command" />
            </div>
            <p>
              3. The command prints the fingerprint of the instance&apos;s signing key. The instance then appears above as
              pending: approve it only if the fingerprint shown there is the one that was printed.
            </p>
            <Button
              size="sm"
              onClick={() => {
                setIssued(null);
                onDone?.();
              }}
            >
              Done, hide the token
            </Button>
          </div>
        )}
      </div>
    </Card>
  );
}
