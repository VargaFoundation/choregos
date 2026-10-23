// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { Button, Card, Code, ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";
import { a2aCardUrl, a2aFileLimit, formatMiB } from "@/lib/integrations";
import type { ApiTokenCreated } from "@/lib/types";

/**
 * Another agent calls Choregos over A2A (ADR 0078): the organisation's card, its endpoint, and a
 * token of the `a2a` scope. Only the workflows that opted in are skills; a decision stays in the
 * console.
 */
export function A2aAgent({ org }: { org: string }) {
  const infos = useQuery({ queryKey: ["integrations"], queryFn: () => api.integrations(), staleTime: Infinity });
  const projects = useQuery({ queryKey: ["projects", org], queryFn: () => api.projects(org) });
  const [token, setToken] = useState<ApiTokenCreated | null>(null);
  const [error, setError] = useState<string | null>(null);
  const card = infos.data ? a2aCardUrl(infos.data.mcp_url, org) : "…";
  const endpoint = card.replace(/\/\.well-known\/agent-card\.json$/, "");

  async function issue() {
    setError(null);
    try {
      setToken(await api.createToken({ name: "A2A client", scopes: ["a2a"], project: null, expires_in_days: 30 }));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "token refused");
    }
  }

  return (
    <Card title="Another agent, over A2A">
      <div className="space-y-2 text-sm">
        <p className="text-ink-muted">
          The workflows that opt in (<Code>a2a:</Code> in their definition) are skills of this organisation&apos;s
          agent. A caller follows each task until a person decides in the console; it never decides itself.
        </p>
        <p className="break-all">
          Agent card <Code data-testid="a2a-card-url">{card}</Code>
        </p>
        <p className="break-all">
          JSON-RPC endpoint <Code data-testid="a2a-endpoint">{endpoint}</Code>
        </p>
        {(projects.data?.items.length ?? 0) > 0 && (
          <div>
            <p>Files a message may carry, each and all together:</p>
            <ul className="ml-4 list-disc" data-testid="a2a-file-limits">
              {projects.data!.items.map((project) => (
                <li key={project.id}>
                  {project.name}: up to {formatMiB(a2aFileLimit(project.config))}{" "}
                  <Link href={`/p/${project.slug}/settings#a2a`} className="text-xs">
                    change it in its settings
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        )}
        {error && <ErrorNote>{error}</ErrorNote>}
        {token ? (
          <p className="break-all">
            Token, shown once (scope <Code>a2a</Code>, 30 days): <Code data-testid="a2a-token">{token.token}</Code>
          </p>
        ) : (
          <Button onClick={() => void issue()}>Create an A2A token</Button>
        )}
        <p className="text-xs text-ink-muted">
          It opens the A2A door only: the REST API and the MCP door refuse it. Revoke it in Admin › My access tokens.
        </p>
      </div>
    </Card>
  );
}
