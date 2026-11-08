// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { UserRound } from "lucide-react";
import { AccessTokensCard } from "@/components/account/access-tokens";
import { ROLE_LABELS } from "@/components/members";
import { PageHeader } from "@/components/page-header";
import { ThemePicker } from "@/components/theme-picker";
import { Card } from "@/components/ui";
import { api } from "@/lib/api";
import { shortDate } from "@/lib/format";
import { isMultiOrg, membershipGroups } from "@/lib/memberships";
import { useSession } from "@/lib/session";

/**
 * My account: what belongs to the signed-in person rather than to the organisation — who they are,
 * their roles, their preferences and their access tokens. The account menu under their name, in the
 * header, leads here; the administration keeps what one administers for everyone.
 */
export default function AccountPage() {
  const { me, org } = useSession();
  const edition = useQuery({ queryKey: ["edition"], queryFn: () => api.edition(), staleTime: Infinity, retry: false });
  const groups = membershipGroups(me?.memberships ?? [], { multiOrg: isMultiOrg(edition.data), currentOrg: org });

  return (
    <div className="space-y-4">
      <PageHeader icon={UserRound} title="My account" />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Profile">
          <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-2 text-sm">
            <dt className="text-ink-muted">Name</dt>
            <dd data-verbatim>{me?.display_name || "—"}</dd>
            <dt className="text-ink-muted">E-mail</dt>
            <dd data-verbatim>{me?.email ?? "—"}</dd>
            <dt className="text-ink-muted">Last sign-in</dt>
            <dd>{me?.last_login_at ? shortDate(me.last_login_at) : "—"}</dd>
          </dl>
          <p className="mt-3 text-xs text-ink-muted">
            Your name and address come from your identity provider: they are refreshed at each sign-in.
          </p>
        </Card>

        <Card title="Roles">
          {groups.map((group) => (
            <div key={group.org ?? "platform"} className="mt-2 first:mt-0">
              {group.org !== null && (
                <p className="text-xs font-medium text-ink" data-verbatim>
                  {group.org}
                </p>
              )}
              <ul
                aria-label={group.org ? `Your roles in ${group.org}` : "Your roles"}
                className="space-y-1 text-xs text-ink-muted"
              >
                {group.lines.map((line) => (
                  <li key={line.scope} data-testid="membership">
                    <span data-verbatim>{line.scope}</span> :{" "}
                    <span className="font-mono" title={ROLE_LABELS[line.role]}>
                      {line.role}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </Card>

        <Card title="Preferences">
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <span className="text-ink-muted">Theme</span>
            <ThemePicker />
          </div>
          <p className="mt-3 text-xs text-ink-muted">Kept in this browser: another browser keeps its own.</p>
        </Card>

        <AccessTokensCard />
      </div>
    </div>
  );
}
