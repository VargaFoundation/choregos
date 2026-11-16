// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { UserRound } from "lucide-react";
import { MyLinkedAccountsCard } from "@/components/accounts/my-linked-accounts";
import { AccessTokensCard } from "@/components/account/access-tokens";
import { AvatarEditor } from "@/components/account/avatar-editor";
import { personName } from "@/components/account/account-menu";
import { SessionsCard } from "@/components/account/sessions";
import { ROLE_LABELS } from "@/components/members";
import { PageHeader } from "@/components/page-header";
import { ThemePicker } from "@/components/theme-picker";
import { Card } from "@/components/ui";
import { api } from "@/lib/api";
import { relative, shortDate } from "@/lib/format";
import { isMultiOrg, membershipGroups } from "@/lib/memberships";
import { useSession } from "@/lib/session";

/** The page's sections, in order: the side index and the cards share these anchors. */
const SECTIONS = [
  { id: "profile", label: "Profile" },
  { id: "sessions", label: "Sessions" },
  { id: "tokens", label: "Access tokens" },
  { id: "linked-accounts", label: "Linked accounts" },
  { id: "roles", label: "Roles" },
  { id: "preferences", label: "Preferences" },
] as const;

/**
 * My account: what belongs to the signed-in person rather than to the organisation — who they are,
 * where they are signed in, their access tokens, their accounts on code forges, their roles and their
 * preferences. The account menu
 * under their name, in the header, leads here; the administration keeps what one administers for
 * everyone.
 */
export default function AccountPage() {
  const { me, org } = useSession();
  const edition = useQuery({ queryKey: ["edition"], queryFn: () => api.edition(), staleTime: Infinity, retry: false });
  const groups = membershipGroups(me?.memberships ?? [], { multiOrg: isMultiOrg(edition.data), currentOrg: org });
  const name = me ? personName(me) : "";
  const fullName = [me?.given_name, me?.family_name].filter(Boolean).join(" ");
  const job = [me?.job_title, me?.department].filter(Boolean).join(" · ");
  const organisations = [...new Set((me?.memberships ?? []).map((m) => m.org))].sort();

  return (
    <div className="space-y-6">
      <PageHeader
        icon={UserRound}
        title="My account"
        description="Who you are here, where you are signed in, the tokens you hold and how the console looks to you."
      />
      <div className="lg:grid lg:grid-cols-[11rem_minmax(0,1fr)] lg:gap-8">
        <nav aria-label="Account sections" className="hidden lg:block">
          <ul className="sticky top-6 space-y-0.5 text-sm">
            {SECTIONS.map((section) => (
              <li key={section.id}>
                <a
                  href={`#${section.id}`}
                  className="block rounded px-2 py-1.5 text-ink-muted no-underline hover:bg-surface-muted hover:text-ink"
                >
                  {section.label}
                </a>
              </li>
            ))}
          </ul>
        </nav>

        <div className="min-w-0 space-y-4">
          <Card title="Profile" id="profile" className="scroll-mt-6">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <p className="truncate text-base font-medium text-ink" data-verbatim>
                  {name || "—"}
                </p>
                {job && (
                  <p className="truncate text-sm text-ink" data-verbatim data-testid="account-job">
                    {job}
                  </p>
                )}
                <p className="truncate text-sm text-ink-muted" data-verbatim>
                  {me?.email ?? "—"}
                </p>
                {me?.id && org && (
                  <p className="mt-1 text-xs">
                    <Link href={`/admin/members/${encodeURIComponent(me.id)}`}>View my profile</Link>
                  </p>
                )}
              </div>
            </div>
            <div className="mt-4">
              <AvatarEditor name={name} url={me?.avatar_url} />
            </div>
            <dl className="mt-4 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-2 text-sm">
              {fullName && fullName !== name && (
                <>
                  <dt className="text-ink-muted">Full name</dt>
                  <dd data-verbatim>{fullName}</dd>
                </>
              )}
              <dt className="text-ink-muted">Organisations</dt>
              <dd data-verbatim>{organisations.length > 0 ? organisations.join(", ") : "—"}</dd>
              <dt className="text-ink-muted">Last sign-in</dt>
              <dd>
                {me?.last_login_at ? (
                  <>
                    {shortDate(me.last_login_at)} <span className="text-ink-muted">({relative(me.last_login_at)})</span>
                  </>
                ) : (
                  "—"
                )}
              </dd>
            </dl>
            <p className="mt-3 text-xs text-ink-muted">
              Your name, address, job title and department come from your identity provider: they are refreshed at each
              sign-in. Your photo is the one you upload here.
            </p>
          </Card>

          <SessionsCard className="scroll-mt-6" />

          <AccessTokensCard className="scroll-mt-6" />

          <MyLinkedAccountsCard className="scroll-mt-6" />

          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="Roles" id="roles" className="scroll-mt-6">
              {groups.map((group) => (
                <div key={group.org ?? "platform"} className="mt-3 first:mt-0">
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

            <Card title="Preferences" id="preferences" className="scroll-mt-6">
              <div className="flex flex-wrap items-center gap-3 text-sm">
                <span className="text-ink-muted">Theme</span>
                <ThemePicker />
              </div>
              <p className="mt-3 text-xs text-ink-muted">Kept in this browser: another browser keeps its own.</p>
            </Card>
          </div>
        </div>
      </div>
    </div>
  );
}
