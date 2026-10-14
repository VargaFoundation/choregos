// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Confirmation } from "@/components/geste-confirme";
import { AdditiveRolesNote, PROJECT_ROLES, ROLE_LABELS, ROLES, peopleOf, rank, type Role } from "@/components/members";
import { Button, Card, Empty, ErrorNote, EtatDeLecture, Input, Select, Table, TBody, TD, TH, THead, TR } from "@/components/ui";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";

/**
 * The members of a scope (S30-06, plan item 9): the organisation — one row per person, their
 * organisation role and the projects they belong to — or one project — its members and their
 * project role. The roles offered are those of the scope: no `org_admin` on a project row. A role
 * chosen in a list applies once confirmed (S23-07).
 */
export function MembersTable({ project }: { project?: string }) {
  const { org, me } = useSession();
  const client = useQueryClient();
  const members = useQuery({ queryKey: ["members", org, project ?? ""], queryFn: () => api.members(org, project) });
  // The project picker of an invitation from the organisation's page: a list, not free text.
  const projects = useQuery({ queryKey: ["projects", org], queryFn: () => api.projects(org), enabled: !project });
  const roles: readonly Role[] = project ? PROJECT_ROLES : ROLES;
  const [invite, setInvite] = useState<{ email: string; role: Role; project: string }>({
    email: "",
    role: "developer",
    project: project ?? "",
  });
  const [toRemove, setToRemove] = useState<string | null>(null);
  const [newRole, setNewRole] = useState<{ key: string; role: Role } | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const self = (email: string) => Boolean(me?.email && email === me.email);

  async function refresh() {
    await client.invalidateQueries({ queryKey: ["members", org] });
  }
  async function act(gesture: () => Promise<unknown>, done: string) {
    setMessage(null);
    try {
      await gesture();
      setMessage(done);
      await refresh();
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "Refused");
    }
  }

  const people = peopleOf(members.data ?? []).map((person) => ({
    ...person,
    // On a project's page, the role shown is the project's.
    role: project ? (person.projects.find((p) => p.slug === project)?.role ?? null) : person.orgRole,
  }));
  const inviteRoles: readonly Role[] = invite.project ? PROJECT_ROLES : ROLES;

  return (
    <Card title={project ? "Members of this project" : `Members of ${org}`}>
      <div className="space-y-4">
        <AdditiveRolesNote />
        <form
          className="flex flex-wrap items-end gap-2 text-sm"
          aria-label="Invite a member"
          onSubmit={(event) => {
            event.preventDefault();
            void act(
              () => api.addMember(org, { email: invite.email, role: invite.role, project_slug: invite.project || null }),
              `${invite.email} invited as ${ROLE_LABELS[invite.role]}${invite.project ? ` on ${invite.project}` : ""}`,
            ).then(() => setInvite({ email: "", role: "developer", project: project ?? "" }));
          }}
        >
          <label className="flex min-w-0 flex-col gap-1">
            <span className="text-xs text-ink-muted">Member e-mail</span>
            <Input
              value={invite.email}
              onChange={(event) => setInvite((i) => ({ ...i, email: event.target.value }))}
              placeholder="alice@example.org"
              className="w-auto"
            />
          </label>
          {!project && (
            <label className="flex min-w-0 flex-col gap-1">
              <span className="text-xs text-ink-muted">Scope</span>
              <Select
                value={invite.project}
                onChange={(event) =>
                  setInvite((i) => ({
                    ...i,
                    project: event.target.value,
                    role: event.target.value && i.role === "org_admin" ? "project_owner" : i.role,
                  }))
                }
                className="w-auto"
              >
                <option value="">The whole organisation</option>
                {(projects.data?.items ?? []).map((p) => (
                  <option key={p.slug} value={p.slug}>
                    {p.name ?? p.slug}
                  </option>
                ))}
              </Select>
            </label>
          )}
          <label className="flex min-w-0 flex-col gap-1">
            <span className="text-xs text-ink-muted">Role</span>
            <Select
              value={invite.role}
              onChange={(event) => setInvite((i) => ({ ...i, role: event.target.value as Role }))}
              className="w-auto"
            >
              {inviteRoles.map((role) => (
                <option key={role} value={role}>
                  {ROLE_LABELS[role]}
                </option>
              ))}
            </Select>
          </label>
          <Button size="sm" tone="primary" type="submit" disabled={!invite.email}>
            Invite
          </Button>
        </form>
        {message && (
          <p className="text-sm" role="status">
            {message}
          </p>
        )}
        {members.error ? (
          <ErrorNote>{String(members.error)}</ErrorNote>
        ) : !members.data ? (
          <EtatDeLecture lecture={members} quoi="the members" />
        ) : people.length === 0 ? (
          <Empty>No member</Empty>
        ) : (
          <Table aria-label={project ? "Members of this project" : `Members of ${org}`} data-testid="membres">
            <THead>
              <TR>
                <TH>Member</TH>
                <TH>{project ? "Project role" : "Organisation role"}</TH>
                {!project && <TH>Projects</TH>}
                <TH>
                  <span className="sr-only">Actions</span>
                </TH>
              </TR>
            </THead>
            <TBody>
              {people.map((person) => {
                const key = person.userId || person.email;
                const shown = newRole?.key === key ? newRole.role : person.role;
                const lowering = person.role != null && newRole?.key === key && rank(newRole.role) < rank(person.role);
                return (
                  <TR key={key} data-testid={`membre-${person.email}`}>
                    <TD>{person.email}</TD>
                    <TD>
                      <Select
                        aria-label={`Role of ${person.email}${project ? ` on ${project}` : ""}`}
                        value={shown ?? ""}
                        onChange={(event) => {
                          const role = event.target.value as Role;
                          setNewRole(role === person.role ? null : { key, role });
                        }}
                        className="w-auto"
                      >
                        {shown == null && <option value="">Project roles only</option>}
                        {roles.map((role) => (
                          <option key={role} value={role}>
                            {ROLE_LABELS[role]}
                          </option>
                        ))}
                      </Select>
                      {newRole?.key === key && (
                        <div className="mt-2 max-w-sm">
                          <Confirmation
                            ton={self(person.email) && lowering ? "danger" : "primary"}
                            question={
                              self(person.email) && lowering
                                ? `This is you: as ${ROLE_LABELS[newRole.role]} you lose what ${ROLE_LABELS[person.role!]} lets you do, and cannot give it back to yourself.`
                                : `Make ${person.email} ${ROLE_LABELS[newRole.role]}${project ? ` on ${project}` : ""}?`
                            }
                            confirmer={`Make ${self(person.email) ? "me" : person.email} ${ROLE_LABELS[newRole.role]}`}
                            action={async () => {
                              await api.addMember(org, { email: person.email, role: newRole.role, project_slug: project ?? null });
                              setMessage(`${person.email} is now ${ROLE_LABELS[newRole.role]}`);
                              await refresh();
                            }}
                            onFait={() => setNewRole(null)}
                            onAnnuler={() => setNewRole(null)}
                          />
                        </div>
                      )}
                    </TD>
                    {!project && (
                      <TD className="text-xs text-ink-muted" title={person.projects.map((p) => `${p.slug}: ${ROLE_LABELS[p.role]}`).join(", ")}>
                        {person.projects.length === 0
                          ? "—"
                          : person.projects.map((p) => (
                              <span key={p.slug} className="mr-2 inline-block">
                                <span data-donnee className="font-mono">
                                  {p.slug}
                                </span>{" "}
                                · {ROLE_LABELS[p.role]}
                              </span>
                            ))}
                      </TD>
                    )}
                    <TD>
                      {person.role == null ? null : toRemove === key ? (
                        <span className="inline-flex gap-2">
                          <Button
                            size="sm"
                            tone="danger"
                            onClick={() =>
                              void act(
                                () => api.removeMember(org, person.userId, project ?? null),
                                `${person.email} removed${project ? ` from ${project}` : ""}`,
                              ).then(() => setToRemove(null))
                            }
                          >
                            Confirm removal
                          </Button>
                          <Button size="sm" onClick={() => setToRemove(null)}>
                            Cancel
                          </Button>
                        </span>
                      ) : (
                        <Button size="sm" onClick={() => setToRemove(key)}>
                          Remove<span className="sr-only"> {person.email}</span>
                        </Button>
                      )}
                    </TD>
                  </TR>
                );
              })}
            </TBody>
          </Table>
        )}
      </div>
    </Card>
  );
}
