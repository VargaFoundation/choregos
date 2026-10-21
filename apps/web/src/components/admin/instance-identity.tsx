// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { Badge, Card, EtatDeLecture } from "@/components/ui";
import { ApiError, api } from "@/lib/api";

/**
 * How people sign in to this instance (S42-03): the deployment's sign-in configuration, read-only.
 * A secret is never shown, only whether it is set. Hidden for a reader who is not a platform
 * administrator (the route answers 403).
 */
export function InstanceIdentityCard() {
  const identity = useQuery({
    queryKey: ["platform-identity"],
    queryFn: () => api.platformIdentity(),
    retry: false,
  });
  if (identity.error instanceof ApiError && identity.error.status === 403) return null;
  return (
    <Card title="How people sign in to this instance" className="lg:col-span-2">
      <table className="w-full text-xs">
        <tbody>
          {(identity.data?.items ?? []).map((row) => (
            <tr key={row.key} className="border-b border-line last:border-0">
              <th scope="row" className="py-1 pr-3 text-left font-normal text-ink-muted">
                {row.label}
              </th>
              <td className="py-1 font-mono" data-donnee>
                {row.value === "missing" ? <Badge tone="danger">missing</Badge> : row.value}
              </td>
              <td className="py-1 pl-3 text-right text-ink-muted">{row.source}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <EtatDeLecture lecture={identity} quoi="the sign-in configuration" />
      <p className="mt-2 text-xs text-ink-muted">
        Set in the deployment (chart global.oidc / global.mcp.oauth); read-only here.
      </p>
    </Card>
  );
}
