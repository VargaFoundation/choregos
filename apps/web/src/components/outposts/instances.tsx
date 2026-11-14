// SPDX-License-Identifier: Apache-2.0
"use client";

import { useId, useState } from "react";
import { ConfirmedGesture } from "@/components/confirmed-gesture";
import { Badge, Button, Card, Code, Empty, Label, Table, TBody, TD, TH, THead, TR } from "@/components/ui";
import { api } from "@/lib/api";
import { relative, shortDate } from "@/lib/format";
import { INSTANCE_LABEL, INSTANCE_TONE, presence, refusalOf, type OutpostRefusal } from "@/lib/outposts";
import type { OutpostInstance } from "@/lib/types";
import { RefusalNote } from "./refusal";

const REVOKE_QUESTION =
  "This is final: its key does not come back for as long as the outpost exists, and nothing in flight at the outpost is taken, from this instance or another. To retire a healthy instance, pause the outpost first and let its calls finish.";

/** An instance's id starts with its outpost's: the end is what tells two instances apart. */
function shortId(id: string): string {
  return id.length > 12 ? `…${id.slice(-12)}` : id;
}

/** What the instance said of itself at enrolment: kept as data, never interpreted. */
function facts(instance: OutpostInstance): string {
  return Object.entries(instance.facts)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([name, value]) => `${name} ${value}`)
    .join(", ");
}

/**
 * The outpost's instances, as the API lists them: pending ones first, because they are what waits
 * for a person. A pending instance is approved here, and only after the person has confirmed that
 * its fingerprint is the one the daemon printed on its own machine.
 */
export function OutpostInstances({
  org,
  slug,
  revoked,
  instances,
  onChange,
}: {
  org: string;
  slug: string;
  /** The outpost is revoked: nothing is approved any more. */
  revoked: boolean;
  instances: OutpostInstance[];
  onChange: () => Promise<void>;
}) {
  const pending = instances.filter((instance) => instance.status === "pending");
  const others = instances.filter((instance) => instance.status !== "pending");
  return (
    <Card title="Instances" data-testid="outpost-instances">
      {instances.length === 0 && <Empty title="No instance yet">Enrol one below: it appears here as pending, with its fingerprint.</Empty>}
      {pending.length > 0 && (
        <ul className="mb-4 space-y-3" aria-label="Instances waiting for approval">
          {pending.map((instance) => (
            <PendingInstance key={instance.id} org={org} slug={slug} instance={instance} blocked={revoked} onChange={onChange} />
          ))}
        </ul>
      )}
      {others.length > 0 && (
        <Table>
          <THead>
            <TR>
              <TH>Instance</TH>
              <TH>Status</TH>
              <TH>Fingerprint</TH>
              <TH>Program</TH>
              <TH>Endpoints</TH>
              <TH>Presence</TH>
              <TH align="right">In flight</TH>
              <TH>Decided</TH>
              <TH>
                <span className="sr-only">Actions</span>
              </TH>
            </TR>
          </THead>
          <TBody>
            {others.map((instance) => (
              <InstanceRow key={instance.id} org={org} slug={slug} instance={instance} onChange={onChange} />
            ))}
          </TBody>
        </Table>
      )}
    </Card>
  );
}

function PendingInstance({
  org,
  slug,
  instance,
  blocked,
  onChange,
}: {
  org: string;
  slug: string;
  instance: OutpostInstance;
  blocked: boolean;
  onChange: () => Promise<void>;
}) {
  const id = useId();
  // The fingerprint the person confirmed, not a tick: what is sent is what was on screen when they said so.
  const [confirmed, setConfirmed] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<OutpostRefusal | null>(null);
  const compared = confirmed !== null && confirmed === instance.fingerprint;
  const said = facts(instance);

  async function approve() {
    if (!compared || confirmed === null) return;
    setBusy(true);
    setRefusal(null);
    try {
      await api.approveOutpostInstance(org, slug, instance.id, { fingerprint: confirmed });
      await onChange();
    } catch (cause) {
      // A session that is too old is sent through the sign-in again by the API client, and comes back
      // to this page; anything else is the API's refusal, said here.
      setRefusal(refusalOf(cause));
      setBusy(false);
    }
  }

  return (
    <li className="space-y-3 rounded border border-line border-l-2 border-l-warn p-3 text-sm" data-testid={`pending-instance-${instance.id}`}>
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <Badge tone={INSTANCE_TONE.pending}>{INSTANCE_LABEL.pending}</Badge>
        <span>
          Enrolled {relative(instance.enrolled_at)}, program {instance.daemon_version}
        </span>
        <span className="font-mono text-xs text-ink-muted" title={instance.id}>
          {shortId(instance.id)}
        </span>
      </p>
      {said && (
        <p className="text-xs text-ink-muted">
          It says of itself, unchecked: <span data-verbatim>{said}</span>
        </p>
      )}
      <EnrolledWith instance={instance} />
      <div>
        <Label>Fingerprint of its signing key</Label>
        <code className="block break-all font-mono text-sm" data-testid="instance-fingerprint">
          {instance.fingerprint}
        </code>
      </div>
      {blocked ? (
        <p className="text-xs text-ink-muted">The outpost is revoked: this instance cannot be approved.</p>
      ) : (
        <>
          <label className="flex items-start gap-2">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={compared}
              onChange={(event) => setConfirmed(event.target.checked ? instance.fingerprint : null)}
            />
            <span>
              I compared them: this is the fingerprint <Code>choregos-outpost enrol</Code> printed on the outpost&apos;s machine.
            </span>
          </label>
          <div className="flex flex-wrap items-start gap-2">
            <Button tone="primary" size="sm" onClick={() => void approve()} disabled={!compared || busy} aria-describedby={`${id}-rule`}>
              {busy ? "Approving…" : "Approve the instance"}
            </Button>
            <ConfirmedGesture
              tonDuBouton="danger"
              question={`Revoke this pending instance? ${REVOKE_QUESTION}`}
              confirmer="Revoke the instance"
              action={async () => {
                await api.revokeOutpostInstance(org, slug, instance.id);
                await onChange();
              }}
            >
              Revoke
            </ConfirmedGesture>
          </div>
          <p id={`${id}-rule`} className="text-xs text-ink-muted">
            Approve only what you compared: a fingerprint that differs is an instance nobody should approve, so revoke it.
            Approving needs a sign-in less than {instance.approval_step_up_minutes} minutes old; if yours is older you are
            asked to sign in again, then to confirm the fingerprint again on this page.
          </p>
        </>
      )}
      {refusal && <RefusalNote refusal={refusal} />}
    </li>
  );
}

/** The token an instance enrolled with, and who issued it: what tells two pending instances apart besides what they say. */
function EnrolledWith({ instance }: { instance: OutpostInstance }) {
  if (!instance.enrolment_id) return null;
  return (
    <p className="text-xs text-ink-muted" data-testid="instance-enrolled-with">
      Enrolled with the token <span className="font-mono" data-verbatim>{instance.enrolment_id}</span>
      {instance.enrolled_by && (
        <>
          , issued by <span data-verbatim>{instance.enrolled_by}</span>
        </>
      )}
    </p>
  );
}

function InstanceRow({ org, slug, instance, onChange }: { org: string; slug: string; instance: OutpostInstance; onChange: () => Promise<void> }) {
  const seen = presence(instance);
  const said = facts(instance);
  const isRevoked = instance.status === "revoked";
  return (
    <TR data-testid={`instance-${instance.id}`}>
      <TD>
        <span className="font-mono text-xs" title={instance.id}>
          {shortId(instance.id)}
        </span>
        {said && (
          <span className="block text-xs text-ink-muted" data-verbatim>
            {said}
          </span>
        )}
      </TD>
      <TD>
        <Badge tone={INSTANCE_TONE[instance.status]}>{INSTANCE_LABEL[instance.status]}</Badge>
      </TD>
      <TD className="font-mono text-xs whitespace-nowrap">{instance.fingerprint}</TD>
      <TD className="text-xs">
        {instance.daemon_version}, protocol {instance.protocol}
      </TD>
      <TD className="text-xs" data-verbatim>
        {instance.endpoints.join(", ") || "—"}
      </TD>
      <TD>
        {isRevoked ? (
          <span className="text-xs text-ink-muted">—</span>
        ) : (
          <Badge tone={seen.online ? "ok" : "neutral"} variant="plain">
            {seen.label}
          </Badge>
        )}
      </TD>
      <TD align="right">{instance.in_flight}</TD>
      <TD className="text-xs">
        {isRevoked ? (
          <>
            Revoked {shortDate(instance.revoked_at)}
            {instance.revoked_by && (
              <>
                {" "}
                by <span data-verbatim>{instance.revoked_by}</span>
              </>
            )}
          </>
        ) : (
          <>
            Approved {shortDate(instance.approved_at)}
            {instance.approved_by && (
              <>
                {" "}
                by <span data-verbatim>{instance.approved_by}</span>
              </>
            )}
          </>
        )}
      </TD>
      <TD>
        {!isRevoked && (
          <ConfirmedGesture
            tonDuBouton="danger"
            question={`Revoke this instance? ${REVOKE_QUESTION}`}
            confirmer="Revoke the instance"
            action={async () => {
              await api.revokeOutpostInstance(org, slug, instance.id);
              await onChange();
            }}
          >
            Revoke
          </ConfirmedGesture>
        )}
      </TD>
    </TR>
  );
}
