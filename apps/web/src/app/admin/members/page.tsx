// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { TBody, TD, TH, THead, TR, Table } from "@varga/design-system";
import { Button, Card, Empty, ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";

const ROLES = ["viewer", "developer", "release_captain", "project_owner", "org_admin"] as const;
type Role = (typeof ROLES)[number];

/**
 * Les membres de l'organisation : inviter, changer un rôle, retirer. Le dernier administrateur
 * ne se retire pas — l'API le refuse (409), et l'écran le dit.
 */
export default function MembersPage() {
  const { org } = useSession();
  const client = useQueryClient();
  const members = useQuery({ queryKey: ["members", org], queryFn: () => api.members(org) });
  const [invite, setInvite] = useState({ email: "", role: "developer" as Role, project_slug: "" });
  const [aRetirer, setARetirer] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function rafraichir() {
    await client.invalidateQueries({ queryKey: ["members", org] });
  }

  async function agir(geste: () => Promise<unknown>, reussite: string) {
    setMessage(null);
    try {
      await geste();
      setMessage(reussite);
      await rafraichir();
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "refused");
    }
  }

  return (
    <Card title={`members of ${org}`}>
      <form
        className="mb-4 flex flex-wrap items-center gap-2 text-sm"
        onSubmit={(event) => {
          event.preventDefault();
          void agir(
            () => api.addMember(org, { email: invite.email, role: invite.role, project_slug: invite.project_slug || null }),
            `${invite.email} invited as ${invite.role}`,
          ).then(() => setInvite({ email: "", role: "developer", project_slug: "" }));
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
          onChange={(event) => setInvite((i) => ({ ...i, role: event.target.value as Role }))}
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
      {message && (
        <p className="mb-3 text-sm" role="status">
          {message}
        </p>
      )}
      {members.error && <ErrorNote>{String(members.error)}</ErrorNote>}
      {(members.data ?? []).length === 0 ? (
        <Empty>no member</Empty>
      ) : (
        <Table aria-label={`members of ${org}`}>
          <THead>
            <TR>
              <TH>member</TH>
              <TH>scope</TH>
              <TH>role</TH>
              <TH>
                <span className="sr-only">actions</span>
              </TH>
            </TR>
          </THead>
          <TBody>
            {(members.data ?? []).map((membership) => {
              const cle = `${membership.user_id}:${membership.project_slug ?? ""}`;
              const qui = membership.email ?? membership.user_id ?? "?";
              return (
                <TR key={cle}>
                  <TD>{qui}</TD>
                  <TD>{membership.project_slug ?? "whole organisation"}</TD>
                  <TD>
                    <select
                      aria-label={`role of ${qui}${membership.project_slug ? ` on ${membership.project_slug}` : ""}`}
                      value={membership.role}
                      onChange={(event) =>
                        void agir(
                          () =>
                            api.addMember(org, {
                              email: membership.email ?? "",
                              role: event.target.value as Role,
                              project_slug: membership.project_slug ?? null,
                            }),
                          `${qui} is now ${event.target.value}`,
                        )
                      }
                      className="rounded border border-line bg-surface px-2 py-1 font-mono text-xs"
                    >
                      {ROLES.map((role) => (
                        <option key={role} value={role}>
                          {role}
                        </option>
                      ))}
                    </select>
                  </TD>
                  <TD>
                    {aRetirer === cle ? (
                      <span className="inline-flex gap-2">
                        <Button
                          size="sm"
                          tone="danger"
                          onClick={() =>
                            void agir(
                              () => api.removeMember(org, membership.user_id ?? "", membership.project_slug),
                              `${qui} removed`,
                            ).then(() => setARetirer(null))
                          }
                        >
                          confirm removal
                        </Button>
                        <Button size="sm" onClick={() => setARetirer(null)}>
                          cancel
                        </Button>
                      </span>
                    ) : (
                      <Button size="sm" onClick={() => setARetirer(cle)}>
                        remove {qui}
                      </Button>
                    )}
                  </TD>
                </TR>
              );
            })}
          </TBody>
        </Table>
      )}
    </Card>
  );
}
