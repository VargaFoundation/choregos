// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { NetworkActivityCard } from "@/components/admin/network-activity";
import { NetworkCeilingCard } from "@/components/admin/network-ceiling";
import { McpGatewayCard } from "@/components/admin/mcp-gateway";
import { OutboundProxyCard } from "@/components/admin/outbound-proxy";
import { PlatformChangeBlock } from "@/components/egress/egress-modal";
import { Alert, Badge, Button, Card, Empty, ErrorNote, EtatDeLecture, Heading, Table, TBody, TD, TH, THead, TR } from "@/components/ui";
import { api } from "@/lib/api";
import { FAMILY_LABEL, hostLabel, outsideCeiling } from "@/lib/egress";
import { relative } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { EgressRule, EgressVerdict } from "@/lib/types";

/**
 * The organisation's network, in one place (S44-04): what its agents may reach (ADR 0070), the hosts
 * Choregos may call for it (S39-22, ADR 0066: who asked, who decided, what the platform must still
 * open; revoking closes a host from the next call), its outbound proxy (ADR 0087) and the network
 * activity. These cards were spread over the administration's overview, next to the session and the
 * tokens, and read as one confused block.
 */
export default function EgressPage() {
  const { org, me } = useSession();
  const orgAdmin = (me?.memberships ?? []).some((m) => m.org === org && !m.project_slug && m.role === "org_admin");
  const client = useQueryClient();
  const overview = useQuery({ queryKey: ["egress", org], queryFn: () => api.orgEgress(org) });
  const [error, setError] = useState<string | null>(null);
  const [changes, setChanges] = useState<EgressVerdict[] | null>(null);
  const data = overview.data;
  const canDecide = !!data?.can_decide;
  const refresh = () => client.invalidateQueries({ queryKey: ["egress", org] });
  async function act(gesture: () => Promise<unknown>) {
    setError(null);
    try {
      await gesture();
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "refused");
    }
  }
  const outside = data ? outsideCeiling(data) : [];
  async function showChanges() {
    setError(null);
    try {
      const result = await api.checkOrgEgress(org, {
        targets: outside.map(({ rule, family }) => ({ host: rule.host, port: rule.ports[0] ?? 443, used_by: [family] })),
        reason: "hosts allowed in this organisation that the platform's network does not open",
      });
      setChanges(result.verdicts);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "refused");
    }
  }
  return (
    <div className="space-y-6">
      <NetworkCeilingCard org={org} canEdit={orgAdmin} />

      <section className="space-y-4" aria-labelledby="network-hosts">
      <Heading as="h2" size="lg" id="network-hosts">
        Hosts Choregos calls
      </Heading>
      <p className="max-w-3xl text-sm text-ink-muted">
        The hosts Choregos may call for {org} — an MCP server, a git repository, a web site. Adding one of those asks
        for the host first; an administrator allows it, anyone else asks. Choregos cannot change the cluster&apos;s
        network policy: what the platform must still open is listed below, never applied.
      </p>
      {error && <ErrorNote>{error}</ErrorNote>}
      <EtatDeLecture lecture={overview} quoi="the network access" />

      {data && (
        <Card title="Deployment" >
          <dl className="grid gap-x-4 gap-y-1 text-sm sm:grid-cols-[auto_1fr]" data-testid="egress-deployment">
            <dt className="text-ink-muted">Mode</dt>
            <dd>
              <Badge tone={data.mode === "enforce" ? "ok" : "warn"}>{data.mode}</Badge>{" "}
              <span className="text-xs text-ink-muted">
                {data.mode === "enforce"
                  ? "a host that is not allowed is refused before it is called"
                  : data.mode === "warn"
                    ? "a host that is not allowed is logged, and called"
                    : "the list is not checked"}
              </span>
            </dd>
            {(["api", "workers", "runners"] as const).map((family) => (
              <FamilyLine key={family} family={family} patterns={data.ceiling[family] ?? []} />
            ))}
          </dl>
          <p className="mt-2 text-xs text-ink-muted">
            The ceiling is what the cluster&apos;s network lets through, as the operator declares it. A family without
            one is not checked here: the cluster still decides.
          </p>
        </Card>
      )}

      {data && (
        <Card title="Requests" action={<span className="text-xs text-ink-muted">{data.requests.length} waiting</span>}>
          {data.requests.length === 0 ? (
            <Empty title="No request waits">Nobody is waiting for a host to be opened.</Empty>
          ) : (
            <ul className="space-y-2" data-testid="egress-pending">
              {data.requests.map((rule) => (
                <li key={rule.id} className="flex flex-wrap items-center gap-3 border-t border-line pt-2 text-sm first:border-t-0 first:pt-0">
                  <span className="font-medium" data-donnee>
                    {rule.host}
                  </span>
                  <span className="text-xs text-ink-muted">
                    {who(rule)} · {rule.reason || rule.origin}
                  </span>
                  {canDecide ? (
                    <span className="flex gap-2">
                      <Button size="sm" tone="primary" onClick={() => void act(() => api.approveOrgEgressRequest(org, rule.id))}>
                        Approve
                      </Button>
                      <Button size="sm" onClick={() => void act(() => api.refuseOrgEgressRequest(org, rule.id))}>
                        Refuse
                      </Button>
                    </span>
                  ) : (
                    <span className="text-xs text-ink-muted">an administrator decides</span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}

      {data && (
        <Card title="Rules" action={<span className="text-xs text-ink-muted">{data.rules.filter((r) => r.status === "allowed").length} allowed</span>}>
          {data.rules.length === 0 ? (
            <Empty title="No host is open">Adding an MCP server, a git skill or a knowledge source opens its host.</Empty>
          ) : (
            <div className="overflow-x-auto">
              <Table data-testid="egress-rules">
                <THead>
                  <TR>
                    <TH>Host</TH>
                    <TH>Called by</TH>
                    <TH>Status</TH>
                    <TH>Origin</TH>
                    <TH>Decided</TH>
                    <TH />
                  </TR>
                </THead>
                <TBody>
                  {data.rules.map((rule) => (
                    <TR key={rule.id}>
                      <TD>
                        <span data-donnee>{hostLabel({ host: rule.host, port: rule.ports[0] ?? 443 })}</span>
                        {rule.ports.length > 1 || (rule.ports[0] ?? 443) !== 443 ? (
                          <span className="text-xs text-ink-muted"> ports {rule.ports.join(", ")}</span>
                        ) : null}
                      </TD>
                      <TD>{rule.used_by.map((f) => FAMILY_LABEL[f]).join(", ")}</TD>
                      <TD>
                        <Badge tone={rule.status === "allowed" ? "ok" : rule.status === "refused" ? "danger" : "neutral"}>
                          {rule.status}
                        </Badge>
                      </TD>
                      <TD className="text-xs">{rule.origin}</TD>
                      <TD className="text-xs">{rule.decided_by ? `${rule.decided_by} ${rule.decided_at ? relative(rule.decided_at) : ""}` : ""}</TD>
                      <TD>
                        {rule.status === "allowed" && canDecide && (
                          <Button size="sm" onClick={() => void act(() => api.revokeOrgEgressRule(org, rule.id))}>
                            Revoke
                          </Button>
                        )}
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </div>
          )}
        </Card>
      )}

      {data && outside.length > 0 && (
        <Card title="What the platform must still open">
          <div className="space-y-3 text-sm" data-testid="egress-platform-list">
            <Alert tone="warn">
              These hosts are allowed here, but the deployment&apos;s ceiling does not cover them: calls to them are
              refused until the platform team opens them.
            </Alert>
            <ul className="list-disc pl-5">
              {outside.map(({ rule, family }) => (
                <li key={`${rule.id}:${family}`}>
                  <span data-donnee>{rule.host}</span> <span className="text-ink-muted">({FAMILY_LABEL[family]})</span>
                </li>
              ))}
            </ul>
            <Button size="sm" onClick={() => void showChanges()}>
              Show the change
            </Button>
            {changes?.map((v) => (
              <PlatformChangeBlock key={`${v.host}:${v.used_by}`} verdict={v} />
            ))}
          </div>
        </Card>
      )}
      </section>

      {orgAdmin && (
        <div className="grid gap-4 lg:grid-cols-2">
          <OutboundProxyCard org={org} canEdit={orgAdmin} />
          <NetworkActivityCard org={org} />
        </div>
      )}
      <McpGatewayCard org={org} canEdit={orgAdmin} />
    </div>
  );
}

function who(rule: EgressRule): string {
  return `asked by ${rule.requested_by ?? "someone"}${rule.requested_at ? ` ${relative(rule.requested_at)}` : ""}`;
}

function FamilyLine({ family, patterns }: { family: "api" | "workers" | "runners"; patterns: string[] }) {
  return (
    <>
      <dt className="text-ink-muted">Ceiling, {FAMILY_LABEL[family]}</dt>
      <dd data-testid={`ceiling-${family}`}>
        {patterns.length === 0 ? <span className="text-ink-muted">not declared</span> : patterns.join(", ")}
      </dd>
    </>
  );
}
