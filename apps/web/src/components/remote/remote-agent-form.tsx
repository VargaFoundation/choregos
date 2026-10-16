// SPDX-License-Identifier: Apache-2.0
"use client";

import { useMutation } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { slugFromName } from "@/components/agents/builder";
import { Alert, Badge, Button, Card, ErrorNote, Field, Input, Select, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { shortDigest } from "@/lib/remote-run";
import type { A2aRuntime, Agent, AgentSpec, RemoteAgentDiscovery } from "@/lib/types";
import { CREDENTIAL_TYPES } from "./credentials";

type Existing = { agent: Agent; spec: AgentSpec };

const AUTH_LABEL: Record<string, string> = { ...CREDENTIAL_TYPES };

/** The number a person typed, or `undefined` for an empty field: a declaration of nothing is not zero. */
function cost(text: string): number | undefined {
  if (text.trim() === "") return undefined;
  const value = Number(text);
  return Number.isFinite(value) && value >= 0 ? value : undefined;
}

/**
 * Register a remote agent (ADR 0062), in two moves: read its card (the platform fetches it, you see what
 * it says), then say how to call it: which skill, which credential, how long, what it costs. Publication
 * pins the card; a card that changes later stops the step until a new version is published. The same
 * form publishes the next version of an existing remote agent, which is how a changed card is accepted.
 */
export function RemoteAgentForm({
  org,
  existing,
  onDone,
  procedure,
}: {
  org: string;
  existing?: Existing;
  onDone: (agent: { slug: string }) => void;
  /** A per-type procedure (Rovo, generic A2A…): a later story fills this place, above the card step. */
  procedure?: React.ReactNode | ((cardUrl: string) => React.ReactNode);
}) {
  const current = existing?.spec.runtime?.kind === "a2a" ? (existing.spec.runtime as A2aRuntime) : undefined;
  const [cardUrl, setCardUrl] = useState(current?.card_url ?? "");
  const [found, setFound] = useState<RemoteAgentDiscovery | null>(null);
  const [name, setName] = useState(existing?.agent.display_name ?? "");
  const [slug, setSlug] = useState(existing?.agent.slug ?? "");
  const [description, setDescription] = useState(existing?.agent.description ?? "");
  const [instructions, setInstructions] = useState(existing?.spec.instructions ?? "");
  const [skill, setSkill] = useState(current?.skill ?? "");
  const [credential, setCredential] = useState(current?.credential ?? "");
  const [minutes, setMinutes] = useState(String(current?.timeout_minutes ?? 30));
  const [perTask, setPerTask] = useState(current?.declared_cost?.per_task_usd?.toString() ?? "");
  const [perMinute, setPerMinute] = useState(current?.declared_cost?.per_minute_usd?.toString() ?? "");
  const [input, setInput] = useState<"text" | "text+files">(current?.input ?? "text");
  const [error, setError] = useState<string | null>(null);

  const read = useMutation({
    mutationFn: () => api.discoverRemoteAgent(org, cardUrl.trim()),
    onSuccess: (discovery) => {
      setFound(discovery);
      setError(null);
      if (!existing) {
        setName((value) => value || discovery.proposed.display_name);
        setSlug((value) => value || discovery.proposed.slug);
        setDescription((value) => value || discovery.proposed.description || "");
      }
      const proposed = discovery.proposed.spec?.runtime as A2aRuntime | undefined;
      setSkill((value) => value || proposed?.skill || "");
      setCredential((value) => value || proposed?.credential || discovery.credentials?.[0] || "");
    },
    onError: (cause) => {
      setFound(null);
      setError(cause instanceof Error ? cause.message : "refused");
    },
  });

  const runtime = (): A2aRuntime => ({
    kind: "a2a",
    card_url: cardUrl.trim(),
    ...(skill && { skill }),
    ...(credential && { credential }),
    timeout_minutes: Math.min(480, Math.max(1, Math.round(Number(minutes) || 30))),
    declared_cost: { per_task_usd: cost(perTask) ?? null, per_minute_usd: cost(perMinute) ?? null },
    input,
  });
  const spec = (): AgentSpec => ({ instructions, runtime: runtime() });

  const publish = useMutation({
    mutationFn: async (lifecycle: "active" | "draft") => {
      if (existing) {
        await api.publishAgentVersion(org, existing.agent.slug, spec());
        return { slug: existing.agent.slug };
      }
      return api.createAgent(org, { slug, kind: "remote", display_name: name, description: description || null, spec: spec(), lifecycle });
    },
    onSuccess: (agent) => onDone(agent),
    onError: (cause) => setError(cause instanceof Error ? cause.message : "refused"),
  });

  const card = found?.card;
  const needsCredential = card ? !card.auth_types?.includes("none") : false;
  const noCredential = Boolean(found) && needsCredential && (found?.credentials?.length ?? 0) === 0;
  const ready =
    Boolean(found) &&
    (existing ? true : /^[a-z][a-z0-9-]{1,62}$/.test(slug) && name.trim().length > 0) &&
    (!needsCredential || credential !== "");

  return (
    <div className="space-y-4" data-testid="remote-agent-form">
      {procedure && <div data-testid="remote-procedure">{typeof procedure === "function" ? procedure(cardUrl) : procedure}</div>}
      <Card title="1. The agent's card">
        <form
          className="flex max-w-3xl flex-wrap items-end gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (cardUrl.trim()) read.mutate();
          }}
        >
          <Field label="Card URL" className="min-w-72 flex-1" hint="https only. The platform reads it; nothing is created yet.">
            {(props) => (
              <Input
                {...props}
                value={cardUrl}
                onChange={(e) => setCardUrl(e.target.value)}
                placeholder="https://a2a.atlassian.com/.well-known/agent.json"
              />
            )}
          </Field>
          <Button type="submit" tone="primary" disabled={!/^https:\/\//.test(cardUrl.trim()) || read.isPending}>
            {read.isPending ? "Reading…" : found ? "Read again" : "Read the card"}
          </Button>
        </form>
        {error && !found && <ErrorNote>{error}</ErrorNote>}
      </Card>

      {card && found && (
        <>
          <Card title="What the card says" action={<Badge tone="accent">Remote · A2A {card.protocol_version}</Badge>} data-testid="card-summary">
            <p className="text-sm font-medium" data-donnee>
              {card.name}
            </p>
            {card.description && <p className="mt-1 max-w-3xl text-xs text-ink-muted">{card.description}</p>}
            <dl className="mt-3 grid gap-x-6 gap-y-1 text-xs sm:grid-cols-2">
              <div>
                <dt className="text-ink-muted">Endpoint</dt>
                <dd className="break-all font-mono">{card.endpoint}</dd>
              </div>
              <div>
                <dt className="text-ink-muted">Digest (pinned at publication)</dt>
                <dd className="font-mono" title={card.digest}>
                  {shortDigest(card.digest)}
                </dd>
              </div>
              <div>
                <dt className="text-ink-muted">Authentication</dt>
                <dd data-testid="card-auth">{(card.auth_types ?? []).map((t) => AUTH_LABEL[t] ?? t).join(", ") || "none this platform supports"}</dd>
              </div>
              <div>
                <dt className="text-ink-muted">Streaming</dt>
                <dd>{card.streaming ? "yes" : "no: the platform polls"}</dd>
              </div>
            </dl>
            <ul className="mt-3 space-y-1 text-xs" aria-label="Skills" data-testid="card-skills">
              {(card.skills ?? []).map((s) => (
                <li key={s.id}>
                  <span className="font-mono">{s.id}</span>
                  {s.description ? <span className="text-ink-muted"> · {s.description}</span> : null}
                </li>
              ))}
            </ul>
          </Card>

          <Card title="2. How to call it">
            <div className="grid max-w-3xl gap-3">
              {!existing && (
                <>
                  <Field label="Name">
                    {(props) => (
                      <Input
                        {...props}
                        value={name}
                        onChange={(e) => {
                          setName(e.target.value);
                          setSlug(slugFromName(e.target.value));
                        }}
                      />
                    )}
                  </Field>
                  <Field label="Identifier" hint="The name workflows give: agent: <identifier>">
                    {(props) => <Input {...props} value={slug} onChange={(e) => setSlug(e.target.value)} className="font-mono" />}
                  </Field>
                  <Field label="Description">
                    {(props) => <Input {...props} value={description} onChange={(e) => setDescription(e.target.value)} />}
                  </Field>
                </>
              )}
              <Field label="Skill" hint="The skill the step asks for. Leave open to let the agent choose.">
                {(props) => (
                  <Select {...props} value={skill} onChange={(e) => setSkill(e.target.value)}>
                    <option value="">Any (the agent chooses)</option>
                    {(card.skills ?? []).map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name || s.id}
                      </option>
                    ))}
                  </Select>
                )}
              </Field>
              <Field label="Credential" hint={needsCredential ? "Whose permissions the agent acts with." : "This card asks for no authentication."}>
                {(props) => (
                  <Select {...props} value={credential} onChange={(e) => setCredential(e.target.value)} disabled={!needsCredential}>
                    <option value="">{needsCredential ? "Choose a credential" : "None"}</option>
                    {(found.credentials ?? []).map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </Select>
                )}
              </Field>
              {noCredential && (
                <div data-testid="no-credential">
                  <Alert tone="warn" title="No credential fits this card">
                    The card accepts {(card.auth_types ?? []).map((t) => AUTH_LABEL[t] ?? t).join(", ")}.{" "}
                    <Link href="/admin/remote-agents" className="underline">
                      Add and connect one in the administration
                    </Link>
                    , then come back.
                  </Alert>
                </div>
              )}
              <div className="grid gap-3 sm:grid-cols-3">
                <Field label="Time limit (minutes)">
                  {(props) => <Input {...props} inputMode="numeric" value={minutes} onChange={(e) => setMinutes(e.target.value)} />}
                </Field>
                <Field label="Cost per task (USD)" hint="What you declare it costs">
                  {(props) => <Input {...props} inputMode="decimal" value={perTask} onChange={(e) => setPerTask(e.target.value)} placeholder="0.05" />}
                </Field>
                <Field label="Cost per minute (USD)">
                  {(props) => <Input {...props} inputMode="decimal" value={perMinute} onChange={(e) => setPerMinute(e.target.value)} placeholder="0" />}
                </Field>
              </div>
              <p className="text-xs text-ink-muted" data-testid="cost-note">
                The protocol reports no cost. What you declare is booked in the cost ledger as external, apart from model spend; with nothing
                declared the ledger says unmeasured, and Choregos does not know what this agent costs.
              </p>
              <Field label="What the step sends">
                {(props) => (
                  <Select {...props} value={input} onChange={(e) => setInput(e.target.value as "text" | "text+files")}>
                    <option value="text">The instructions and the task, as text</option>
                    <option value="text+files">The same, and the step&apos;s declared inputs as files</option>
                  </Select>
                )}
              </Field>
              <Field label="Instructions" hint="Sent ahead of the task. The project's memory is never sent to a remote agent.">
                {(props) => <Textarea {...props} rows={4} value={instructions} onChange={(e) => setInstructions(e.target.value)} />}
              </Field>
              <p className="text-xs text-ink-muted" data-testid="egress-note">
                Each project that uses this agent must allow <span className="font-mono">{(found.domains ?? [card.endpoint]).join(", ")}</span> in its
                policy (<span className="font-mono">sandbox.network.allow_domains</span>): the step is refused otherwise.
              </p>
              <div className="flex flex-wrap items-center gap-3">
                <Button tone="primary" disabled={!ready || publish.isPending} onClick={() => publish.mutate("active")}>
                  {existing ? `Publish version ${existing.agent.latest_version + 1}` : "Publish the agent"}
                </Button>
                {!existing && (
                  <Button disabled={!ready || publish.isPending} onClick={() => publish.mutate("draft")}>
                    Save as draft
                  </Button>
                )}
                {error && <ErrorNote>{error}</ErrorNote>}
              </div>
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
