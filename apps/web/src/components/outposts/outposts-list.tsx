// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Alert, Badge, Button, Card, Empty, Field, Input, Lead, ReadState, Table, TBody, TD, TH, THead, TR } from "@/components/ui";
import { api } from "@/lib/api";
import { deploymentReadiness, OUTPOST_TONE, presence, refusalOf, stateLabel, type OutpostRefusal } from "@/lib/outposts";
import type { Outpost } from "@/lib/types";
import { RefusalNote } from "./refusal";

/** A slug as the API takes it: what connectors route by. */
const SLUG = /^[a-z0-9][a-z0-9-]{0,62}$/;

/**
 * The organisation's outposts: places a person enrols, which only dial out to the platform. The
 * platform stores no address and no credential of an outpost's side, so the list shows none: what an
 * outpost is called, where it stands, how many instances it has, and whether one of them is there.
 */
export function OutpostsList({ org }: { org: string }) {
  const outposts = useQuery({ queryKey: ["outposts", org], queryFn: () => api.outposts(org) });
  const items = outposts.data?.items ?? [];
  // What the deployment would refuse, said before anyone tries (S65-24).
  const readiness = deploymentReadiness(outposts.data);
  const unready = readiness.create ?? readiness.enrol;
  return (
    <div className="space-y-4">
      <Lead>
        An outpost is a place of {org} that the platform cannot reach, a network behind a firewall for example. A small
        program installed there opens every connection itself, takes the tool calls routed to it and answers them; the
        platform never connects to it. An organisation that already runs an MCP gateway the platform can reach needs none.
      </Lead>
      <ReadState lecture={outposts} quoi="the outposts" />
      {unready && (
        <div data-testid="outposts-deployment-readiness">
          <Alert tone="warn" title={readiness.create ? "Outposts are switched off" : "No enrolment token can be issued"}>
            {unready}
          </Alert>
        </div>
      )}
      {outposts.data && items.length === 0 && (
        <Empty title="No outpost yet">Create one below, then enrol its first instance with a token.</Empty>
      )}
      {items.length > 0 && (
        <Card title="Outposts" padding="sm">
          <Table data-testid="outposts">
            <THead>
              <TR>
                <TH>Outpost</TH>
                <TH>Status</TH>
                <TH>Instances</TH>
                <TH>Presence</TH>
                <TH>Endpoints</TH>
                <TH>Connectors</TH>
              </TR>
            </THead>
            <TBody>
              {items.map((outpost) => (
                <OutpostRow key={outpost.slug} outpost={outpost} />
              ))}
            </TBody>
          </Table>
        </Card>
      )}
      <NewOutpost org={org} switchedOff={readiness.create !== null} />
    </div>
  );
}

function OutpostRow({ outpost }: { outpost: Outpost }) {
  const seen = presence(outpost);
  const { pending, active, revoked } = outpost.instances;
  return (
    <TR data-testid={`outpost-${outpost.slug}`}>
      <TD>
        <Link href={`/admin/outposts/${outpost.slug}`} className="font-medium" data-verbatim>
          {outpost.display_name}
        </Link>
        {outpost.display_name !== outpost.slug && (
          <span className="block font-mono text-xs text-ink-muted" data-verbatim>
            {outpost.slug}
          </span>
        )}
      </TD>
      <TD>
        <Badge tone={OUTPOST_TONE[outpost.status]}>{stateLabel(outpost.status)}</Badge>
      </TD>
      <TD>
        <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span>{active} approved</span>
          {pending > 0 ? <Badge tone="warn">{pending} pending</Badge> : <span className="text-ink-muted">0 pending</span>}
          <span className="text-ink-muted">{revoked} revoked</span>
        </span>
      </TD>
      <TD>
        <Badge tone={seen.online ? "ok" : "neutral"} variant="plain">
          {seen.label}
        </Badge>
      </TD>
      <TD className="text-xs" data-verbatim>
        {outpost.endpoints.join(", ") || "—"}
      </TD>
      <TD className="text-xs" data-verbatim>
        {outpost.connectors.join(", ") || "—"}
      </TD>
    </TR>
  );
}

/** A new outpost has no instance: its page is where the first one is enrolled, so the creation leads there. */
function NewOutpost({ org, switchedOff }: { org: string; switchedOff: boolean }) {
  const client = useQueryClient();
  const router = useRouter();
  const [slug, setSlug] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<OutpostRefusal | null>(null);
  const wrong = slug !== "" && !SLUG.test(slug);

  async function create() {
    setBusy(true);
    setRefusal(null);
    try {
      const created = await api.createOutpost(org, { slug, ...(name.trim() ? { display_name: name.trim() } : {}) });
      await client.invalidateQueries({ queryKey: ["outposts", org] });
      router.push(`/admin/outposts/${created.slug}`);
    } catch (cause) {
      setRefusal(refusalOf(cause));
      setBusy(false);
    }
  }

  return (
    <Card title="New outpost">
      <form
        className="space-y-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (SLUG.test(slug) && !busy) void create();
        }}
      >
        <div className="grid gap-3 sm:grid-cols-2 [&>*]:min-w-0">
          <Field
            label="Slug"
            hint="What connectors route by: lower case letters, digits and hyphens."
            error={wrong ? "Lower case letters, digits and hyphens, 63 at most, not starting with a hyphen." : null}
          >
            {(props) => <Input {...props} value={slug} onChange={(event) => setSlug(event.target.value)} autoComplete="off" placeholder="factory-floor" />}
          </Field>
          <Field label="Name" hint="What people read; the slug when left empty.">
            {(props) => <Input {...props} value={name} onChange={(event) => setName(event.target.value)} autoComplete="off" maxLength={200} />}
          </Field>
        </div>
        <Button tone="primary" type="submit" disabled={!SLUG.test(slug) || busy || switchedOff}>
          Create the outpost
        </Button>
        {refusal && <RefusalNote refusal={refusal} />}
      </form>
    </Card>
  );
}
