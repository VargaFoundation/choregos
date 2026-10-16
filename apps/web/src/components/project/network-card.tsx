// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { Badge, Card, EtatDeLecture } from "@/components/ui";
import { api } from "@/lib/api";

/**
 * The network a project's runs really get (ADR 0070): its own `sandbox.network` under the
 * organisation's ceiling, plus the host of its repository, which a run needs to work at all. Read-only:
 * the project's side is written in its policy (below), the organisation's on the Administration page.
 */
export function ProjectNetworkCard({ slug }: { slug: string }) {
  const network = useQuery({ queryKey: ["project-network", slug], queryFn: () => api.projectNetwork(slug), retry: false });
  const effective = network.data?.effective;
  return (
    <Card title="Network access" className="lg:col-span-2">
      <div className="space-y-3 text-sm" data-testid="project-network">
        <EtatDeLecture lecture={network} quoi="the network access" />
        {effective && (
          <>
            <div>
              <h3 className="text-xs font-medium text-ink-muted">Allowed hosts</h3>
              {effective.allow.length === 0 && effective.implied.length === 0 ? (
                <p>No internet: a run reaches no host outside the platform.</p>
              ) : (
                <ul className="mt-1 flex flex-wrap gap-1.5">
                  {effective.allow.map((host) => (
                    <li key={host}>
                      <Badge tone="neutral">
                        <span className="font-mono text-xs">{host}</span>
                      </Badge>
                    </li>
                  ))}
                  {effective.implied.map((host) => (
                    <li key={`implied-${host}`}>
                      <Badge tone="accent">
                        <span className="font-mono text-xs">{host}</span> · repository
                      </Badge>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div>
              <h3 className="text-xs font-medium text-ink-muted">Denied hosts</h3>
              {effective.deny.length === 0 ? (
                <p className="text-ink-muted">None.</p>
              ) : (
                <ul className="mt-1 flex flex-wrap gap-1.5">
                  {effective.deny.map((host) => (
                    <li key={host}>
                      <Badge tone="danger">
                        <span className="font-mono text-xs">{host}</span>
                      </Badge>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
              <dt className="text-ink-muted">Requests per minute, per host</dt>
              <dd>{effective.per_host_per_minute ?? "no cap"}</dd>
              <dt className="text-ink-muted">Requests per minute, per run</dt>
              <dd>{effective.per_run_per_minute ?? "no cap"}</dd>
              <dt className="text-ink-muted">Built-in web tools</dt>
              <dd>{effective.builtin_web_tools ? "Allowed" : "Off"}</dd>
            </dl>
            {(network.data?.clamps ?? []).length > 0 && (
              <div data-testid="network-clamps">
                <p>The organisation&apos;s network access brings this project down:</p>
                <ul className="list-disc pl-5 text-xs">
                  {(network.data?.clamps ?? []).map((clamp) => (
                    <li key={clamp}>{clamp}</li>
                  ))}
                </ul>
              </div>
            )}
            <p className="text-xs text-ink-muted">
              Edit <code>sandbox.network</code> in the policy below; the organisation&apos;s part is on the Administration page.
            </p>
          </>
        )}
      </div>
    </Card>
  );
}
