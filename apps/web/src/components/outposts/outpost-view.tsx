// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ConfirmedGesture } from "@/components/confirmed-gesture";
import { Extrait } from "@/components/integrations/panel";
import { Alert, Badge, Button, Card, Code, ReadState } from "@/components/ui";
import { api } from "@/lib/api";
import { shortDate } from "@/lib/format";
import { deploymentReadiness, OUTPOST_TONE, POLICY_FILE, policyLines, presence, refusalOf, stateLabel, type OutpostRefusal } from "@/lib/outposts";
import type { Outpost } from "@/lib/types";
import { OutpostCalls } from "./calls";
import { EnrolInstance } from "./enrol-instance";
import { OutpostInstances } from "./instances";
import { OpenTokens } from "./open-tokens";
import { RefusalNote } from "./refusal";

/** While a person waits for the instance they are enrolling, its row is looked for every few seconds. */
const WAITING_MS = 5_000;

/**
 * One outpost: where it stands, its instances (a pending one is approved here, against its
 * fingerprint), the door's keys its policy file pins, and the recent calls routed to it.
 */
export function OutpostView({ org, slug }: { org: string; slug: string }) {
  const client = useQueryClient();
  const [waiting, setWaiting] = useState(false);
  const outpost = useQuery({
    queryKey: ["outpost", org, slug],
    queryFn: () => api.outpost(org, slug),
    refetchInterval: waiting ? WAITING_MS : false,
  });
  const instances = useQuery({
    queryKey: ["outpost-instances", org, slug],
    queryFn: () => api.outpostInstances(org, slug),
    refetchInterval: waiting ? WAITING_MS : false,
  });
  // The list says what the deployment would refuse (S65-24): read once, and shared with the list's page.
  const deployment = useQuery({ queryKey: ["outposts", org], queryFn: () => api.outposts(org) });
  const readiness = deploymentReadiness(deployment.data);
  const refresh = async () => {
    await client.invalidateQueries({ queryKey: ["outpost", org, slug] });
    await client.invalidateQueries({ queryKey: ["outpost-instances", org, slug] });
    await client.invalidateQueries({ queryKey: ["outpost-enrolments", org, slug] });
    await client.invalidateQueries({ queryKey: ["outposts", org] });
  };
  const current = outpost.data;
  return (
    <div className="space-y-4">
      <ReadState lecture={outpost} quoi="the outpost" />
      {current && (
        <>
          <Standing outpost={current} />
          <Summary org={org} outpost={current} onChange={refresh} />
          <ReadState lecture={instances} quoi="the instances" />
          {instances.data && (
            <OutpostInstances org={org} slug={slug} revoked={current.status === "revoked"} instances={instances.data.items} onChange={refresh} />
          )}
          {current.status !== "revoked" && (
            <EnrolInstance
              org={org}
              slug={slug}
              unavailable={readiness.enrol}
              onIssued={() => {
                setWaiting(true);
                void client.invalidateQueries({ queryKey: ["outpost-enrolments", org, slug] });
              }}
              onDone={() => setWaiting(false)}
            />
          )}
          <OpenTokens org={org} slug={slug} />
          <KeysToPin outpost={current} />
          <OutpostCalls org={org} slug={slug} />
        </>
      )}
    </div>
  );
}

/** A paused or a revoked outpost says so before anything else: nothing is handed to it. */
function Standing({ outpost }: { outpost: Outpost }) {
  if (outpost.status === "paused") {
    return (
      <div data-testid="outpost-paused">
        <Alert tone="warn" title="This outpost is paused">
          Nothing more is handed to it, and a call routed to it is refused. What its instances hold may finish. Resume it to
          hand it calls again.
        </Alert>
      </div>
    );
  }
  if (outpost.status === "revoked") {
    return (
      <div data-testid="outpost-revoked">
        <Alert tone="danger" title="This outpost is revoked">
          That is final: every instance of it is revoked and nothing is handed to it. Connectors that still route to it are
          refused until they are routed elsewhere. It can be deleted once none does.
        </Alert>
      </div>
    );
  }
  return null;
}

function Summary({ org, outpost, onChange }: { org: string; outpost: Outpost; onChange: () => Promise<void> }) {
  const client = useQueryClient();
  const router = useRouter();
  const [refusal, setRefusal] = useState<OutpostRefusal | null>(null);
  const [busy, setBusy] = useState(false);
  const seen = presence(outpost);
  const { pending, active, revoked } = outpost.instances;

  async function resume() {
    setBusy(true);
    setRefusal(null);
    try {
      await api.resumeOutpost(org, outpost.slug);
      await onChange();
    } catch (cause) {
      setRefusal(refusalOf(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title={outpost.display_name}
      verbatimTitle
      action={<Badge tone={OUTPOST_TONE[outpost.status]}>{stateLabel(outpost.status)}</Badge>}
      data-testid="outpost-summary"
    >
      <dl className="grid gap-x-4 gap-y-1 text-sm sm:grid-cols-[auto_1fr]">
        <dt className="text-ink-muted">Slug</dt>
        <dd className="font-mono text-xs" data-verbatim>
          {outpost.slug}
        </dd>
        <dt className="text-ink-muted">Presence</dt>
        <dd data-testid="outpost-presence">
          <Badge tone={seen.online ? "ok" : "neutral"} variant="plain">
            {seen.label}
          </Badge>
        </dd>
        <dt className="text-ink-muted">Instances</dt>
        <dd>
          {active} approved, {pending} pending, {revoked} revoked
        </dd>
        <dt className="text-ink-muted">Endpoints announced</dt>
        <dd data-verbatim>{outpost.endpoints.join(", ") || "—"}</dd>
        <dt className="text-ink-muted">Connectors that route to it</dt>
        <dd data-verbatim>{outpost.connectors.join(", ") || "—"}</dd>
        <dt className="text-ink-muted">Epoch</dt>
        <dd>
          {outpost.epoch} <span className="text-xs text-ink-muted">moved by every revocation: what was in flight before it is not taken</span>
        </dd>
        <dt className="text-ink-muted">Created</dt>
        <dd>
          {shortDate(outpost.created_at)}
          {outpost.created_by && (
            <>
              {" "}
              by <span data-verbatim>{outpost.created_by}</span>
            </>
          )}
        </dd>
      </dl>
      <div className="mt-4 flex flex-wrap items-start gap-2">
        {outpost.status === "active" && (
          <ConfirmedGesture
            ton="primary"
            question={`Pause ${outpost.slug}? Nothing more is handed to it and a call routed to it is refused until it is resumed. What its instances hold may finish.`}
            confirmer="Pause the outpost"
            action={async () => {
              await api.pauseOutpost(org, outpost.slug);
              await onChange();
            }}
          >
            Pause
          </ConfirmedGesture>
        )}
        {outpost.status === "paused" && (
          <Button size="sm" tone="primary" onClick={() => void resume()} disabled={busy}>
            Resume
          </Button>
        )}
        {outpost.status !== "revoked" && (
          <ConfirmedGesture
            tonDuBouton="danger"
            question={`Revoke ${outpost.slug}? This is final. Every instance of it is revoked, nothing they had in flight is taken, and a write among it ends in doubt. Connectors that route to it are refused until they are routed elsewhere. To hold a healthy outpost, pause it instead.`}
            confirmer="Revoke the outpost"
            action={async () => {
              await api.revokeOutpost(org, outpost.slug);
              await onChange();
            }}
          >
            Revoke
          </ConfirmedGesture>
        )}
        <ConfirmedGesture
          tonDuBouton="danger"
          question={`Delete ${outpost.slug}? Its instances, the memory of their keys and what is left of its calls go with it, and none of it comes back. An outpost is deleted once it is revoked and no connector routes to it.`}
          confirmer="Delete the outpost"
          action={async () => {
            try {
              await api.deleteOutpost(org, outpost.slug);
            } catch (cause) {
              // The API's own sentence, with the connectors it names when the outpost is still in use.
              const said = refusalOf(cause);
              throw new Error(said.connectors.length > 0 ? `${said.detail} (${said.connectors.join(", ")})` : said.detail);
            }
            await client.invalidateQueries({ queryKey: ["outposts", org] });
            router.push("/admin/outposts");
          }}
        >
          Delete
        </ConfirmedGesture>
      </div>
      {refusal && (
        <div className="mt-3">
          <RefusalNote refusal={refusal} />
        </div>
      )}
    </Card>
  );
}

/**
 * The door's origin and public keys, as the lines of the policy file that name and pin them: an outpost
 * dials no other origin and obeys no other key.
 */
function KeysToPin({ outpost }: { outpost: Outpost }) {
  return (
    <Card title="Keys to pin" data-testid="outpost-door-keys">
      {outpost.door_keys.length === 0 ? (
        <p className="text-sm text-ink-muted">
          There is no key to pin yet: this deployment&apos;s door has no signing key, so no instance can be enrolled. Its
          operator sets one.
        </p>
      ) : (
        <div className="space-y-2 text-sm">
          <p className="text-ink-muted">
            An outpost makes only the calls signed by a key its own policy file pins (<Code>{POLICY_FILE}</Code> by default).
            The first key signs now; any other is announced for a rotation and is added before the door signs with it. Nothing
            the platform sends changes that list.
          </p>
          <Extrait code={policyLines(outpost.control_plane, outpost.door_keys)} testId="outpost-door-keys-line" />
        </div>
      )}
    </Card>
  );
}
