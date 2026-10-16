// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button, Card, ErrorNote, Field, Input, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import type { Agent } from "@/lib/types";
import { readOnly } from "./registre";
import { TagEditor, TagList } from "./tags";

/**
 * An agent's name, description and tags (S40-10): the metadata that is not part of a version. Editing
 * them publishes nothing and moves no checksum. Whoever may publish the agent may edit them; for the
 * others there is no button, and the page's header already carries the server's sentence (`cannot_reason`).
 * Self-contained, so a page can host it anywhere; `bare` drops the card around it (the Definition tab's
 * About group already frames it).
 */
export function AgentDetailsCard({ org, agent, bare = false }: { org: string; agent: Agent; bare?: boolean }) {
  const client = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(agent.display_name);
  const [description, setDescription] = useState(agent.description ?? "");
  const [tags, setTags] = useState<string[]>(agent.tags ?? []);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const everyAgent = useQuery({ queryKey: ["agents", org], queryFn: () => api.agents(org), enabled: editing });
  const suggestions = [...new Set((everyAgent.data ?? []).flatMap((a) => a.tags ?? []))].sort();
  const locked = readOnly(agent) || agent.status === "revoked";

  function open() {
    setName(agent.display_name);
    setDescription(agent.description ?? "");
    setTags(agent.tags ?? []);
    setError(null);
    setEditing(true);
  }

  async function save() {
    setError(null);
    setBusy(true);
    try {
      await api.updateAgent(org, agent.slug, {
        display_name: name.trim() || agent.display_name,
        description: description.trim() || null,
        tags,
      });
      await client.invalidateQueries({ queryKey: ["agent", org, agent.slug] });
      await client.invalidateQueries({ queryKey: ["agents", org] });
      setEditing(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Refused");
    } finally {
      setBusy(false);
    }
  }

  const edit =
    !editing && !locked ? (
      <Button size="sm" onClick={open}>
        Edit details
      </Button>
    ) : undefined;
  const content = (
    <div className="space-y-3" data-testid="agent-details">
      {editing ? (
        <form
          className="space-y-3"
          aria-label="Edit details"
          onSubmit={(event) => {
            event.preventDefault();
            if (!busy) void save();
          }}
        >
          <Field label="Name">{(field) => <Input {...field} value={name} onChange={(e) => setName(e.target.value)} required />}</Field>
          <Field label="Description" hint="What the agent is for, in a sentence or two. People read it in the registry.">
            {(field) => <Textarea {...field} rows={3} maxLength={4000} value={description} onChange={(e) => setDescription(e.target.value)} />}
          </Field>
          <TagEditor value={tags} onChange={setTags} suggestions={suggestions} />
          <p className="text-xs text-ink-muted">Editing these publishes no version: they describe how people find the agent, not what runs.</p>
          {error && <ErrorNote>{error}</ErrorNote>}
          <div className="flex gap-2">
            <Button type="submit" tone="primary" disabled={busy}>
              Save details
            </Button>
            <Button onClick={() => setEditing(false)} disabled={busy}>
              Cancel
            </Button>
          </div>
        </form>
      ) : (
        <>
          <dl className="grid gap-3 text-sm sm:grid-cols-[8rem_minmax(0,1fr)]">
            <dt className="text-xs text-ink-muted">Name</dt>
            <dd data-donnee>{agent.display_name}</dd>
            <dt className="text-xs text-ink-muted">Description</dt>
            <dd className="max-w-3xl text-ink-muted">{agent.description ?? "No description."}</dd>
            <dt className="text-xs text-ink-muted">Tags</dt>
            <dd>{(agent.tags ?? []).length > 0 ? <TagList tags={agent.tags} link /> : <span className="text-ink-muted">No tag.</span>}</dd>
          </dl>
        </>
      )}
    </div>
  );
  if (bare)
    return (
      <div className="space-y-3">
        {edit && <div className="flex justify-end">{edit}</div>}
        {content}
      </div>
    );
  return (
    <Card title="Details" action={edit}>
      {content}
    </Card>
  );
}
