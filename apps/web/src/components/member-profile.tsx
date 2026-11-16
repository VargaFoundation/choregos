// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { BriefcaseBusiness, Clock3, Inbox, KeyRound, Link2, Play, ScrollText, ShieldCheck } from "lucide-react";
import { Avatar } from "@/components/accounts/avatar";
import { MemberLinkedAccounts } from "@/components/accounts/member-linked-accounts";
import { SourceBadge } from "@/components/groups/source-badge";
import { ROLE_LABELS } from "@/components/members";
import { Badge, Card, Empty, ErrorNote, ReadState, StateBadge, Table, TBody, TD, TH, THead, TR } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { relative, shortDate, usd } from "@/lib/format";
import type { MemberProfile } from "@/lib/types";

/** How the account came (ADR 0162). */
const ORIGIN_LABELS: Record<NonNullable<MemberProfile["origin"]>, string> = {
  identity_provider: "Identity provider sign-in",
  provisioned: "Provisioned by the identity provider",
  manual: "Added by an administrator",
};

/** The person's name: given and family name when the identity provider sent them, else the display name. */
export function fullName(profile: Pick<MemberProfile, "given_name" | "family_name" | "display_name" | "email">): string {
  const parts = [profile.given_name, profile.family_name].map((p) => p?.trim()).filter(Boolean);
  return parts.length > 0 ? parts.join(" ") : profile.display_name.trim() || profile.email;
}

/**
 * One member's profile (S66-42, ADR 0162), from one read. Every member of the organisation sees the card:
 * who the person is, their roles and their groups. An administrator, and the person themself, also see the
 * account, the sessions and the work; an administrator alone sees the administration section.
 */
export function MemberProfileView({
  org,
  userId,
  canManage,
  isSelf,
}: {
  org: string;
  userId: string;
  /** An organisation administrator: declares and removes the member's linked accounts. */
  canManage: boolean;
  isSelf: boolean;
}) {
  const profile = useQuery({ queryKey: ["member-profile", org, userId], queryFn: () => api.memberProfile(org, userId) });

  if (profile.error instanceof ApiError && profile.error.status === 404) {
    return <Empty title="No such member">This person is not a member of {org}.</Empty>;
  }
  if (profile.error instanceof ApiError && profile.error.status === 403) {
    return <ErrorNote>Reading a member&apos;s profile requires being a member of {org}.</ErrorNote>;
  }
  if (!profile.data) return <ReadState lecture={profile} quoi="the profile" />;
  const p = profile.data;

  return (
    <div className="space-y-4" data-testid="member-profile">
      <ProfileHeader profile={p} />
      <div className="grid gap-4 lg:grid-cols-2">
        <AccessCard profile={p} />
        {(canManage || isSelf) && p.status === "active" && (
          <Card title="Linked accounts" icon={Link2} data-testid="profile-linked-accounts">
            <MemberLinkedAccounts org={org} userId={p.user_id} readOnly={!canManage} />
            {!canManage && (
              <p className="mt-2 text-xs text-ink-muted">
                You link your accounts from <Link href="/account#linked-accounts">My account</Link>.
              </p>
            )}
          </Card>
        )}
      </div>
      {p.work && <WorkCards work={p.work} />}
      {p.administration && <AdministrationCard administration={p.administration} userId={p.user_id} />}
    </div>
  );
}

function ProfileHeader({ profile: p }: { profile: MemberProfile }) {
  const name = fullName(p);
  const role = [p.job_title, p.department].filter(Boolean).join(" · ");
  return (
    <Card padding="lg" data-testid="profile-header">
      <div className="flex flex-col gap-5 sm:flex-row sm:items-center">
        <Avatar name={name} url={p.avatar_url} size="lg" />
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="truncate text-xl font-semibold tracking-tight text-ink" data-verbatim>
              {name}
            </h2>
            {p.status === "active" ? (
              <Badge tone="ok" data-testid="profile-status">
                Active
              </Badge>
            ) : (
              <Badge tone="neutral" data-testid="profile-status">
                No longer in the organisation
              </Badge>
            )}
          </div>
          {name !== p.display_name && p.display_name && (
            <p className="text-sm text-ink-muted">
              Display name <span data-verbatim className="text-ink">{p.display_name}</span>
            </p>
          )}
          {role && (
            <p className="text-sm text-ink" data-verbatim data-testid="profile-job">
              {role}
            </p>
          )}
          <p className="truncate text-sm text-ink-muted">
            <a href={`mailto:${p.email}`} data-verbatim>
              {p.email}
            </a>
          </p>
        </div>
      </div>
      {p.view === "full" && (
        <dl className="mt-6 grid gap-4 border-t border-line pt-4 text-sm sm:grid-cols-3" data-testid="profile-account">
          <Fact label="Account" icon={ShieldCheck}>
            {p.origin ? ORIGIN_LABELS[p.origin] : "—"}
          </Fact>
          <Fact label="Last sign-in" icon={Clock3}>
            {p.last_login_at ? (
              <span title={shortDate(p.last_login_at)}>{relative(p.last_login_at)}</span>
            ) : (
              "Never signed in"
            )}
          </Fact>
          <Fact label="Open sessions" icon={KeyRound}>
            {p.sessions ? (
              <>
                <span data-testid="profile-sessions">{p.sessions.open}</span>
                {p.sessions.last_activity_at && (
                  <span className="text-ink-muted"> · last activity {relative(p.sessions.last_activity_at)}</span>
                )}
              </>
            ) : (
              "—"
            )}
          </Fact>
        </dl>
      )}
    </Card>
  );
}

function Fact({ label, icon: Icon, children }: { label: string; icon: typeof Clock3; children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-2.5">
      <Icon aria-hidden className="mt-0.5 size-4 shrink-0 text-ink-muted" />
      <div className="min-w-0">
        <dt className="text-xs text-ink-muted">{label}</dt>
        <dd className="text-ink">{children}</dd>
      </div>
    </div>
  );
}

function AccessCard({ profile: p }: { profile: MemberProfile }) {
  return (
    <Card title="Access" icon={ShieldCheck} data-testid="profile-access">
      <dl className="grid grid-cols-[max-content_1fr] items-start gap-x-6 gap-y-3 text-sm">
        <dt className="text-ink-muted">Organisation role</dt>
        <dd>{p.org_role ? <span title={p.org_role}>{ROLE_LABELS[p.org_role]}</span> : <span className="text-ink-muted">None</span>}</dd>
        <dt className="text-ink-muted">Projects</dt>
        <dd>
          {p.project_roles.length === 0 ? (
            <span className="text-ink-muted">None</span>
          ) : (
            <ul aria-label="Project roles" className="space-y-1">
              {p.project_roles.map((r) => (
                <li key={r.project_slug}>
                  <Link href={`/p/${r.project_slug}`} data-verbatim>
                    {r.project_name || r.project_slug}
                  </Link>{" "}
                  <span className="text-ink-muted">· {ROLE_LABELS[r.role]}</span>
                </li>
              ))}
            </ul>
          )}
        </dd>
        <dt className="text-ink-muted">Groups</dt>
        <dd>
          {p.groups.length === 0 ? (
            <span className="text-ink-muted">None</span>
          ) : (
            <ul aria-label="Groups" className="space-y-1.5">
              {p.groups.map((g) => (
                <li key={`${g.source}:${g.slug}`} className="flex flex-wrap items-center gap-2" data-testid="profile-group">
                  {g.source === "identity_provider" ? (
                    <span className="font-mono text-xs" data-verbatim>
                      {g.name}
                    </span>
                  ) : (
                    <Link href={`/admin/groups/${encodeURIComponent(g.slug)}`} data-verbatim>
                      {g.name}
                    </Link>
                  )}
                  <SourceBadge source={g.source} />
                </li>
              ))}
            </ul>
          )}
        </dd>
      </dl>
    </Card>
  );
}

function WorkCards({ work }: { work: NonNullable<MemberProfile["work"]> }) {
  return (
    <div className="grid gap-4 lg:grid-cols-2" data-testid="profile-work">
      <Card
        title="Waiting on them"
        icon={Inbox}
        action={
          work.spend_month_usd != null ? (
            <span className="text-xs text-ink-muted" data-testid="profile-spend">
              Assistant spend this month <span className="font-medium text-ink">{usd(work.spend_month_usd)}</span>
            </span>
          ) : undefined
        }
      >
        {work.pending_requests.length === 0 ? (
          <p className="text-sm text-ink-muted">Nothing waits on them.</p>
        ) : (
          <ul aria-label="Requests waiting on them" className="divide-y divide-line text-sm">
            {work.pending_requests.map((r) => (
              <li key={r.id} className="flex flex-wrap items-center justify-between gap-2 py-2" data-testid="profile-pending">
                <span className="min-w-0">
                  <Link href={`/p/${r.project_slug}/items/${r.work_item_id}`} className="font-medium" data-verbatim>
                    {r.work_item_key}
                  </Link>{" "}
                  <span className="text-ink-muted" data-verbatim>
                    {r.work_item_title}
                  </span>
                </span>
                <span className="flex items-center gap-2 text-xs text-ink-muted">
                  {r.via === "person" ? (
                    <Badge tone="accent">To them</Badge>
                  ) : (
                    <Badge tone="info">
                      Via <span data-verbatim>{r.group}</span>
                    </Badge>
                  )}
                  <span title={shortDate(r.requested_at)}>{relative(r.requested_at)}</span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>
      <Card title="Recent decisions" icon={BriefcaseBusiness}>
        {work.decisions.length === 0 ? (
          <p className="text-sm text-ink-muted">No decision yet.</p>
        ) : (
          <ul aria-label="Recent decisions" className="divide-y divide-line text-sm">
            {work.decisions.map((d) => (
              <li key={d.request_id} className="flex flex-wrap items-center justify-between gap-2 py-2" data-testid="profile-decision">
                <span className="min-w-0">
                  <Link href={`/p/${d.project_slug}/items/${d.work_item_id}`} className="font-medium" data-verbatim>
                    {d.work_item_key}
                  </Link>{" "}
                  <span className="text-ink-muted" data-verbatim>
                    {d.work_item_title}
                  </span>
                </span>
                <span className="flex items-center gap-2 text-xs text-ink-muted">
                  {d.approved === true ? (
                    <Badge tone="ok">Approved</Badge>
                  ) : d.approved === false ? (
                    <Badge tone="danger">Rejected</Badge>
                  ) : (
                    <Badge tone="neutral">Answered</Badge>
                  )}
                  <span title={shortDate(d.decided_at)}>{relative(d.decided_at)}</span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>
      <Card title="Runs they started" icon={Play} className="lg:col-span-2">
        {work.runs.length === 0 ? (
          <p className="text-sm text-ink-muted">No run of a work item they started.</p>
        ) : (
          <Table aria-label="Runs they started">
            <THead>
              <TR>
                <TH>Run</TH>
                <TH>Work item</TH>
                <TH>Stage</TH>
                <TH>Status</TH>
                <TH>Started</TH>
                <TH className="text-right">Cost</TH>
              </TR>
            </THead>
            <TBody>
              {work.runs.map((run) => (
                <TR key={run.id} data-testid="profile-run">
                  <TD>
                    <Link href={`/p/${run.project_slug}/runs/${run.id}`} className="font-mono text-xs" data-verbatim>
                      {run.id.slice(0, 8)}
                    </Link>
                  </TD>
                  <TD>
                    <Link href={`/p/${run.project_slug}/items/${run.work_item_id}`} data-verbatim>
                      {run.work_item_key}
                    </Link>
                  </TD>
                  <TD className="font-mono text-xs" data-verbatim>
                    {run.stage_role}
                  </TD>
                  <TD>
                    <StateBadge state={run.status} />
                  </TD>
                  <TD className="text-ink-muted">{relative(run.started_at)}</TD>
                  <TD className="text-right">{usd(run.cost_usd)}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </Card>
    </div>
  );
}

function AdministrationCard({
  administration,
  userId,
}: {
  administration: NonNullable<MemberProfile["administration"]>;
  userId: string;
}) {
  return (
    <Card
      title="Administration"
      icon={ScrollText}
      data-testid="profile-administration"
      action={
        <span className="text-xs text-ink-muted">
          API tokens <span className="font-medium text-ink" data-testid="profile-tokens">{administration.api_tokens}</span>
        </span>
      }
    >
      {administration.audit.length === 0 ? (
        <p className="text-sm text-ink-muted">No audit event names them.</p>
      ) : (
        <Table aria-label="Recent audit events">
          <THead>
            <TR>
              <TH>Action</TH>
              <TH>Their part</TH>
              <TH>When</TH>
            </TR>
          </THead>
          <TBody>
            {administration.audit.map((entry) => (
              <TR key={entry.id} data-testid="profile-audit">
                <TD className="font-mono text-xs" data-verbatim>
                  {entry.action}
                </TD>
                <TD className="text-ink-muted">{entry.actor_id === userId ? "Actor" : "Target"}</TD>
                <TD className="text-ink-muted" title={shortDate(entry.ts)}>
                  {relative(entry.ts)}
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      )}
    </Card>
  );
}
