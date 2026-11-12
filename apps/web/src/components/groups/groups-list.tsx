// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button, Card, Empty, ErrorNote, Field, Input, Lead, ReadState, Table, TBody, TD, TH, THead, TR } from "@/components/ui";
import { api } from "@/lib/api";
import type { OrgGroup } from "@/lib/types";
import { SourceBadge } from "./source-badge";

/** A group's slug as the API takes it: what a workflow's human actor names. */
export const GROUP_SLUG = /^[a-z][a-z0-9-]{1,62}$/;

/**
 * The organisation's own groups (ADR 0152): each with its source and how many members it holds. Every
 * member of the organisation reads them; the administrator creates `internal` ones here. A group the
 * identity provider pushes (`scim`) is listed too, read-only.
 */
export function GroupsList({ org, canManage }: { org: string; canManage: boolean }) {
  const groups = useQuery({ queryKey: ["org-groups", org], queryFn: () => api.orgGroups(org) });
  const items = groups.data?.items ?? [];
  return (
    <div className="space-y-4">
      <Lead>
        A workflow addresses a step to a group (<code>group: release-captains</code>), an action&apos;s approval names the
        groups that may decide. A group of {org} counts there for whoever is in it, as do the groups the identity provider
        sends at sign-in.
      </Lead>
      <ReadState lecture={groups} quoi="the groups" />
      {groups.data && items.length === 0 && (
        <Empty title="No group yet">{canManage ? "Create one below, then add members of the organisation to it." : "An administrator of the organisation creates them."}</Empty>
      )}
      {items.length > 0 && (
        <Card title="Groups" padding="sm">
          <Table data-testid="org-groups">
            <THead>
              <TR>
                <TH>Group</TH>
                <TH>Source</TH>
                <TH align="right">Members</TH>
                <TH>Description</TH>
              </TR>
            </THead>
            <TBody>
              {items.map((group) => (
                <GroupRow key={group.slug} group={group} />
              ))}
            </TBody>
          </Table>
        </Card>
      )}
      {canManage && <NewGroup org={org} />}
    </div>
  );
}

function GroupRow({ group }: { group: OrgGroup }) {
  return (
    <TR data-testid={`group-${group.slug}`}>
      <TD>
        <Link href={`/admin/groups/${group.slug}`} className="font-medium" data-verbatim>
          {group.name}
        </Link>
        {group.name !== group.slug && (
          <span className="block font-mono text-xs text-ink-muted" data-verbatim>
            {group.slug}
          </span>
        )}
      </TD>
      <TD>
        <SourceBadge source={group.source} />
      </TD>
      <TD align="right" data-testid="member-count">
        {group.member_count}
      </TD>
      <TD className="text-xs text-ink-muted" data-verbatim>
        {group.description || "—"}
      </TD>
    </TR>
  );
}

/** A new group is empty: its page is where members are added, so the creation leads there. */
function NewGroup({ org }: { org: string }) {
  const client = useQueryClient();
  const router = useRouter();
  const [slug, setSlug] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const wrong = slug !== "" && !GROUP_SLUG.test(slug);

  async function create() {
    setBusy(true);
    setError(null);
    try {
      const created = await api.createOrgGroup(org, { slug, name: name.trim() || slug });
      await client.invalidateQueries({ queryKey: ["org-groups", org] });
      router.push(`/admin/groups/${created.slug}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      setBusy(false);
    }
  }

  return (
    <Card title="New group">
      <form
        className="space-y-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (GROUP_SLUG.test(slug) && !busy) void create();
        }}
      >
        <div className="grid gap-3 sm:grid-cols-2 [&>*]:min-w-0">
          <Field
            label="Slug"
            hint="What a workflow names: lower case letters, digits and hyphens."
            error={wrong ? "A lower case letter first, then lower case letters, digits and hyphens: 2 to 63 characters." : null}
          >
            {(props) => <Input {...props} value={slug} onChange={(event) => setSlug(event.target.value)} autoComplete="off" placeholder="release-captains" />}
          </Field>
          <Field label="Name" hint="What people read; the slug when left empty.">
            {(props) => <Input {...props} value={name} onChange={(event) => setName(event.target.value)} autoComplete="off" maxLength={200} />}
          </Field>
        </div>
        <Button tone="primary" type="submit" disabled={!GROUP_SLUG.test(slug) || busy}>
          Create the group
        </Button>
        {error && <ErrorNote>{error}</ErrorNote>}
      </form>
    </Card>
  );
}
