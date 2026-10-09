// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { TBody, TD, TH, THead, TR, Table } from "@varga/design-system";
import { Button, Card, Empty, ErrorNote } from "@/components/ui";
import { Confirmation } from "@/components/geste-confirme";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";

const ROLES = ["viewer", "developer", "release_captain", "project_owner", "org_admin"] as const;
type Role = (typeof ROLES)[number];

/**
 * Les membres de l'organisation : inviter, changer un rôle, retirer. Le dernier administrateur
 * ne se retire pas — l'API le refuse (409), et l'écran le dit.
 */
export default function MembersPage() {
  const { org, me } = useSession();
  const client = useQueryClient();
  const members = useQuery({ queryKey: ["members", org], queryFn: () => api.members(org) });
  const [invite, setInvite] = useState({ email: "", role: "developer" as Role, project_slug: "" });
  const [aRetirer, setARetirer] = useState<string | null>(null);
  // Un rôle choisi dans la liste n'est appliqué qu'une fois confirmé (S23-07) : avant, il partait au
  // changement, y compris le sien — un administrateur se rétrogradait d'un mauvais clic.
  const [nouveauRole, setNouveauRole] = useState<{ cle: string; role: Role } | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const soi = (email: string | null | undefined) => Boolean(me?.email && email === me.email);
  const rang = (role: Role) => ROLES.indexOf(role);

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
        className="mb-4 flex flex-wrap items-end gap-2 text-sm"
        onSubmit={(event) => {
          event.preventDefault();
          void agir(
            () =>
              api.addMember(org, { email: invite.email, role: invite.role, project_slug: invite.project_slug || null }),
            `${invite.email} invited as ${invite.role}`,
          ).then(() => setInvite({ email: "", role: "developer", project_slug: "" }));
        }}
      >
        <label className="flex min-w-0 flex-col gap-1">
          <span className="text-xs text-ink-muted">member e-mail</span>
          <input
            value={invite.email}
            onChange={(event) => setInvite((i) => ({ ...i, email: event.target.value }))}
            placeholder="alice@example.org"
            className="rounded border border-line bg-surface px-2 py-1"
          />
        </label>
        <label className="flex min-w-0 flex-col gap-1">
          <span className="text-xs text-ink-muted">role</span>
          <select
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
        </label>
        <label className="flex min-w-0 flex-col gap-1">
          <span className="text-xs text-ink-muted">project (optional)</span>
          <input
            value={invite.project_slug}
            onChange={(event) => setInvite((i) => ({ ...i, project_slug: event.target.value }))}
            placeholder="project (empty = whole organisation)"
            className="rounded border border-line bg-surface px-2 py-1"
          />
        </label>
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
                      value={nouveauRole?.cle === cle ? nouveauRole.role : membership.role}
                      onChange={(event) => {
                        const role = event.target.value as Role;
                        setNouveauRole(role === membership.role ? null : { cle, role });
                      }}
                      className="rounded border border-line bg-surface px-2 py-1 font-mono text-xs"
                    >
                      {ROLES.map((role) => (
                        <option key={role} value={role}>
                          {role}
                        </option>
                      ))}
                    </select>
                    {nouveauRole?.cle === cle && (
                      <div className="mt-2 max-w-sm">
                        <Confirmation
                          ton={
                            soi(membership.email) && rang(nouveauRole.role) < rang(membership.role)
                              ? "danger"
                              : "primary"
                          }
                          question={
                            soi(membership.email) && rang(nouveauRole.role) < rang(membership.role)
                              ? `this is you: as ${nouveauRole.role} you lose what ${membership.role} lets you do, and cannot give it back to yourself.`
                              : `make ${qui} ${nouveauRole.role}${membership.project_slug ? ` on ${membership.project_slug}` : ""}?`
                          }
                          confirmer={`make ${soi(membership.email) ? "me" : qui} ${nouveauRole.role}`}
                          action={async () => {
                            await api.addMember(org, {
                              email: membership.email ?? "",
                              role: nouveauRole.role,
                              project_slug: membership.project_slug ?? null,
                            });
                            setMessage(`${qui} is now ${nouveauRole.role}`);
                            await rafraichir();
                          }}
                          onFait={() => setNouveauRole(null)}
                          onAnnuler={() => setNouveauRole(null)}
                        />
                      </div>
                    )}
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
