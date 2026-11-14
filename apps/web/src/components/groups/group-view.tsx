// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ConfirmedGesture } from "@/components/confirmed-gesture";
import { peopleOf } from "@/components/members";
import { Alert, Button, Card, Empty, ErrorNote, Field, Input, ReadState, Select, Table, TBody, TD, TH, THead, TR, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import type { OrgGroup, OrgGroupMember } from "@/lib/types";
import { SourceBadge } from "./source-badge";

const message = (cause: unknown) => (cause instanceof Error ? cause.message : String(cause));

/**
 * One group of the organisation (ADR 0160): what it is, who is in it, and, for an `internal` group and an
 * administrator, the gestures that change it. A group the identity provider pushes is read-only here:
 * it is changed where it comes from, and the page says so.
 */
export function GroupView({ org, slug, canManage }: { org: string; slug: string; canManage: boolean }) {
  const client = useQueryClient();
  const group = useQuery({ queryKey: ["org-group", org, slug], queryFn: () => api.orgGroup(org, slug) });
  const members = useQuery({ queryKey: ["org-group-members", org, slug], queryFn: () => api.orgGroupMembers(org, slug) });
  const refresh = async () => {
    await client.invalidateQueries({ queryKey: ["org-group", org, slug] });
    await client.invalidateQueries({ queryKey: ["org-group-members", org, slug] });
    await client.invalidateQueries({ queryKey: ["org-groups", org] });
  };
  const current = group.data;
  const writable = canManage && current?.source === "internal";
  return (
    <div className="space-y-4">
      <ReadState lecture={group} quoi="the group" />
      {current && (
        <>
          {current.source === "scim" && (
            <div data-testid="group-read-only">
              <Alert tone="info" title="This group comes from the identity provider">
                Its name and its members are pushed by the identity provider over SCIM, and changed there. It is read-only
                here.
              </Alert>
            </div>
          )}
          <Summary org={org} group={current} writable={writable} onChange={refresh} />
          <ReadState lecture={members} quoi="the members" />
          {members.data && <Members org={org} group={current} members={members.data.items} writable={writable} onChange={refresh} />}
          {writable && <AddMember org={org} slug={slug} inGroup={members.data?.items ?? []} onChange={refresh} />}
        </>
      )}
    </div>
  );
}

function Summary({ org, group, writable, onChange }: { org: string; group: OrgGroup; writable: boolean; onChange: () => Promise<void> }) {
  const client = useQueryClient();
  const router = useRouter();
  const [editing, setEditing] = useState(false);
  return (
    <Card title={group.name} verbatimTitle action={<SourceBadge source={group.source} />} data-testid="group-summary">
      <dl className="grid gap-x-4 gap-y-1 text-sm sm:grid-cols-[auto_1fr]">
        <dt className="text-ink-muted">Slug</dt>
        <dd className="font-mono text-xs" data-verbatim>
          {group.slug}
        </dd>
        <dt className="text-ink-muted">Description</dt>
        <dd data-verbatim>{group.description || "—"}</dd>
        <dt className="text-ink-muted">In a workflow</dt>
        <dd>
          <code>group: {group.slug}</code>
        </dd>
      </dl>
      {writable && !editing && (
        <div className="mt-3 flex flex-wrap gap-2">
          <Button size="sm" onClick={() => setEditing(true)}>
            Edit
          </Button>
          <ConfirmedGesture
            tonDuBouton="danger"
            question={`Delete ${group.slug}? Its members leave it, and a workflow that names it warns again unless the identity provider sends the group.`}
            confirmer="Delete the group"
            action={async () => {
              await api.deleteOrgGroup(org, group.slug);
              await client.invalidateQueries({ queryKey: ["org-groups", org] });
              router.push("/admin/groups");
            }}
          >
            Delete
          </ConfirmedGesture>
        </div>
      )}
      {writable && editing && <EditGroup org={org} group={group} onDone={() => setEditing(false)} onChange={onChange} />}
    </Card>
  );
}

function EditGroup({ org, group, onDone, onChange }: { org: string; group: OrgGroup; onDone: () => void; onChange: () => Promise<void> }) {
  const [name, setName] = useState(group.name);
  const [description, setDescription] = useState(group.description ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await api.updateOrgGroup(org, group.slug, { name: name.trim(), description: description.trim() || null });
      await onChange();
      onDone();
    } catch (cause) {
      setError(message(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      className="mt-3 space-y-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (name.trim() && !busy) void save();
      }}
    >
      <Field label="Name">{(props) => <Input {...props} value={name} onChange={(event) => setName(event.target.value)} maxLength={200} />}</Field>
      <Field label="Description">
        {(props) => <Textarea {...props} value={description} onChange={(event) => setDescription(event.target.value)} maxLength={2000} rows={2} />}
      </Field>
      <div className="flex gap-2">
        <Button tone="primary" type="submit" size="sm" disabled={!name.trim() || busy}>
          Save
        </Button>
        <Button size="sm" onClick={onDone}>
          Cancel
        </Button>
      </div>
      {error && <ErrorNote>{error}</ErrorNote>}
    </form>
  );
}

function Members({
  org,
  group,
  members,
  writable,
  onChange,
}: {
  org: string;
  group: OrgGroup;
  members: OrgGroupMember[];
  writable: boolean;
  onChange: () => Promise<void>;
}) {
  const [error, setError] = useState<string | null>(null);
  if (members.length === 0) {
    return (
      <Empty title="Nobody in this group">
        A step addressed to it waits for an organisation administrator until someone is in it.
      </Empty>
    );
  }
  async function remove(member: OrgGroupMember) {
    setError(null);
    try {
      await api.removeOrgGroupMember(org, group.slug, member.user_id);
      await onChange();
    } catch (cause) {
      setError(message(cause));
    }
  }
  return (
    <Card title="Members" padding="sm">
      <Table data-testid="group-members">
        <THead>
          <TR>
            <TH>Person</TH>
            <TH>Added by</TH>
            {writable && <TH align="right">Remove</TH>}
          </TR>
        </THead>
        <TBody>
          {members.map((member) => (
            <TR key={member.user_id} data-testid={`group-member-${member.email}`}>
              <TD data-verbatim>{member.email}</TD>
              <TD className="text-xs text-ink-muted" data-verbatim>
                {member.added_by ?? "—"}
              </TD>
              {writable && (
                <TD align="right">
                  <Button size="sm" aria-label={`Remove ${member.email}`} onClick={() => void remove(member)}>
                    Remove
                  </Button>
                </TD>
              )}
            </TR>
          ))}
        </TBody>
      </Table>
      {error && <ErrorNote>{error}</ErrorNote>}
    </Card>
  );
}

/** Only a member of the organisation joins its groups: the choice is among them. */
function AddMember({ org, slug, inGroup, onChange }: { org: string; slug: string; inGroup: OrgGroupMember[]; onChange: () => Promise<void> }) {
  const memberships = useQuery({ queryKey: ["members", org, ""], queryFn: () => api.members(org) });
  const [chosen, setChosen] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const already = new Set(inGroup.map((m) => m.user_id));
  const candidates = peopleOf(memberships.data ?? []).filter((p) => p.userId && !already.has(p.userId));

  async function add() {
    setBusy(true);
    setError(null);
    try {
      await api.addOrgGroupMember(org, slug, { user_id: chosen });
      setChosen("");
      await onChange();
    } catch (cause) {
      setError(message(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Add a member">
      <ReadState lecture={memberships} quoi="the organisation's members" />
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (chosen && !busy) void add();
        }}
      >
        <Field label="Member of the organisation" className="min-w-64">
          {(props) => (
            <Select {...props} value={chosen} onChange={(event) => setChosen(event.target.value)} data-testid="member-choice">
              <option value="">{candidates.length ? "Choose a person…" : "Everyone is already in it"}</option>
              {candidates.map((person) => (
                <option key={person.userId} value={person.userId}>
                  {person.email}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Button tone="primary" type="submit" disabled={!chosen || busy}>
          Add to the group
        </Button>
      </form>
      {error && <ErrorNote>{error}</ErrorNote>}
    </Card>
  );
}
