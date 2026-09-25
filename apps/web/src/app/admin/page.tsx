"use client";

import { Heading } from "@varga/design-system";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button, Card, Empty, ErrorNote, StateBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { shortDate } from "@/lib/format";
import { useSession } from "@/lib/session";

const ROLES = ["viewer", "developer", "release_captain", "project_owner", "org_admin"] as const;

/**
 * Administration : la session, les membres de l'organisation, mes jetons d'API, les
 * backends et exécuteurs de la plateforme, le journal d'audit.
 *
 * Avant : la session et l'audit, en lecture. Tout ce que l'API permettait déjà — inviter
 * un membre, frapper un jeton, voir les backends — n'avait pas d'écran.
 */
export default function AdminPage() {
  const { me, org } = useSession();
  const queryClient = useQueryClient();
  const audit = useQuery({ queryKey: ["audit"], queryFn: () => api.audit() });
  const members = useQuery({ queryKey: ["members", org], queryFn: () => api.members(org) });
  const tokens = useQuery({ queryKey: ["tokens"], queryFn: () => api.myTokens() });
  const backends = useQuery({ queryKey: ["backends"], queryFn: () => api.backends() });
  const executors = useQuery({ queryKey: ["executors"], queryFn: () => api.executors() });
  const [error, setError] = useState<string | null>(null);
  const [invite, setInvite] = useState({ email: "", role: "developer", project_slug: "" });
  const [tokenName, setTokenName] = useState("");
  const [tokenClair, setTokenClair] = useState<string | null>(null);

  async function inviter() {
    setError(null);
    try {
      await api.addMember(org, {
        email: invite.email,
        role: invite.role as (typeof ROLES)[number],
        project_slug: invite.project_slug || null,
      });
      setInvite({ email: "", role: "developer", project_slug: "" });
      void queryClient.invalidateQueries({ queryKey: ["members", org] });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "invitation refused");
    }
  }

  async function frapper() {
    setError(null);
    try {
      const created = await api.createToken({ name: tokenName || "cli", expires_in_days: 90 });
      setTokenClair(created.token);
      setTokenName("");
      void queryClient.invalidateQueries({ queryKey: ["tokens"] });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "token refused");
    }
  }

  return (
    <div className="space-y-4">
      <Heading as="h1" size="xl">
        administration
      </Heading>
      {error && <ErrorNote>{error}</ErrorNote>}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="session">
          <p className="text-sm">
            {me?.display_name} — {me?.email}
          </p>
          <ul className="mt-2 space-y-1 text-xs text-ink-muted">
            {(me?.memberships ?? []).map((membership, index) => (
              <li key={index}>
                {membership.org}
                {membership.project_slug ? `/${membership.project_slug}` : ""} : {membership.role}
              </li>
            ))}
          </ul>
        </Card>

        <Card title="my API tokens">
          {/* Le clair n'existe qu'ici, une fois : il n'est jamais stocké ni réaffiché. */}
          {tokenClair && (
            <div className="mb-3 border border-line border-l-2 border-l-accent bg-surface p-3 text-sm">
              <p className="text-xs text-ink-muted">copy it now — it will not be shown again</p>
              <code className="block break-all font-mono text-xs">{tokenClair}</code>
            </div>
          )}
          <form
            className="mb-3 flex items-center gap-2 text-sm"
            onSubmit={(event) => {
              event.preventDefault();
              void frapper();
            }}
          >
            <input
              aria-label="token name"
              value={tokenName}
              onChange={(event) => setTokenName(event.target.value)}
              placeholder="cli"
              className="rounded border border-line bg-surface px-2 py-1"
            />
            <Button size="sm" tone="primary" type="submit">
              mint a token (90 days)
            </Button>
          </form>
          <ul className="space-y-1 text-xs">
            {(tokens.data ?? []).map((token) => (
              <li key={token.id} className="flex items-center justify-between gap-2">
                <span>
                  <span className="font-mono">{token.name}</span>
                  <span className="text-ink-muted">
                    {" "}
                    · expires {token.expires_at ? shortDate(token.expires_at) : "never"} · last used{" "}
                    {token.last_used_at ? shortDate(token.last_used_at) : "—"}
                  </span>
                </span>
                <Button
                  size="sm"
                  tone="danger"
                  onClick={() =>
                    void api.revokeToken(token.id).then(() => queryClient.invalidateQueries({ queryKey: ["tokens"] }))
                  }
                >
                  revoke
                </Button>
              </li>
            ))}
          </ul>
          {(tokens.data ?? []).length === 0 && <Empty>no token</Empty>}
        </Card>

        <Card title={`members of ${org}`} className="lg:col-span-2">
          <form
            className="mb-3 flex flex-wrap items-center gap-2 text-sm"
            onSubmit={(event) => {
              event.preventDefault();
              void inviter();
            }}
          >
            <input
              aria-label="member e-mail"
              value={invite.email}
              onChange={(event) => setInvite((i) => ({ ...i, email: event.target.value }))}
              placeholder="alice@example.org"
              className="rounded border border-line bg-surface px-2 py-1"
            />
            <select
              aria-label="role"
              value={invite.role}
              onChange={(event) => setInvite((i) => ({ ...i, role: event.target.value }))}
              className="rounded border border-line bg-surface px-2 py-1"
            >
              {ROLES.map((role) => (
                <option key={role} value={role}>
                  {role}
                </option>
              ))}
            </select>
            <input
              aria-label="project (optional)"
              value={invite.project_slug}
              onChange={(event) => setInvite((i) => ({ ...i, project_slug: event.target.value }))}
              placeholder="project (empty = whole organisation)"
              className="rounded border border-line bg-surface px-2 py-1"
            />
            <Button size="sm" tone="primary" type="submit" disabled={!invite.email}>
              invite
            </Button>
          </form>
          <table>
            <thead>
              <tr>
                <th>member</th>
                <th>scope</th>
                <th>role</th>
              </tr>
            </thead>
            <tbody>
              {(members.data ?? []).map((membership, index) => (
                <tr key={index}>
                  <td className="text-xs">{membership.email ?? membership.user_id}</td>
                  <td className="text-xs text-ink-muted">{membership.project_slug ?? "whole organisation"}</td>
                  <td className="font-mono text-xs">{membership.role}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {(members.data ?? []).length === 0 && <Empty>no member</Empty>}
        </Card>

        <Card title="agent backends">
          <ul className="space-y-1 text-xs">
            {(backends.data ?? []).map((backend) => (
              <li key={backend.name} className="flex items-center gap-2">
                <StateBadge
                  state={backend.enabled ? "ok" : "disabled"}
                  display={backend.enabled ? "enabled" : "disabled"}
                  kind={backend.enabled ? "terminal" : "blocked"}
                />
                <span className="font-mono">{backend.name}</span>
                <span className="text-ink-muted">{backend.version ?? ""}</span>
              </li>
            ))}
          </ul>
          {(backends.data ?? []).length === 0 && <Empty>no backend registered</Empty>}
        </Card>

        <Card title="executors">
          <ul className="space-y-1 text-xs">
            {(executors.data ?? []).map((executor) => (
              <li key={executor.kind} className="flex items-center gap-2">
                <span className="font-mono">{executor.kind}</span>
                <span className="text-ink-muted">{executor.enabled === false ? "disabled" : "enabled"}</span>
              </li>
            ))}
          </ul>
          {(executors.data ?? []).length === 0 && <Empty>no executor registered</Empty>}
        </Card>

        <Card title="audit log" className="lg:col-span-2">
          <table>
            <thead>
              <tr>
                <th>when</th>
                <th>actor</th>
                <th>action</th>
                <th>target</th>
              </tr>
            </thead>
            <tbody>
              {(audit.data?.items ?? []).map((entry) => (
                <tr key={entry.id}>
                  <td className="text-xs text-ink-muted">{shortDate(entry.ts)}</td>
                  <td>
                    <StateBadge state={entry.actor_kind} display={entry.actor_kind} kind="work" />{" "}
                    <span className="text-xs">{entry.actor_id ?? "—"}</span>
                  </td>
                  <td className="font-mono text-xs">{entry.action}</td>
                  <td className="text-xs text-ink-muted">
                    {entry.target_type}
                    {entry.target_id ? ` ${entry.target_id.slice(0, 8)}` : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {(audit.data?.items ?? []).length === 0 && <Empty>no audit entry</Empty>}
        </Card>
      </div>
    </div>
  );
}
