// SPDX-License-Identifier: Apache-2.0
"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Badge, Button, Card, ErrorNote, Field, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { type ProjectConfigValue, useProjectConfigPatch } from "@/lib/project-config";
import type { Trigger, TriggerAck, TriggerToken } from "@/lib/types";

/** What a manual firing did, in words (the received event's outcome). */
const OUTCOME: Record<string, string> = {
  created_work_item: "started a new work item",
  already_open: "already open: same key",
  ignored: "ignored: the payload does not match `when`",
};

/**
 * A project's HTTP triggers (S51-02, ADR 0113): the URL another system calls, the workflow it opens,
 * a token minted once, and a firing by hand with a sample payload. The triggers themselves are
 * declared in the project's configuration (`triggers:`); this card never shows a token twice.
 */
export function Triggers({ slug }: { slug: string }) {
  const listed = useQuery({ queryKey: ["triggers", slug], queryFn: () => api.triggers(slug) });
  // An answer that is not a list (an older API, a stand-in) shows nothing rather than breaking the page.
  const triggers = Array.isArray(listed.data) ? listed.data : [];
  return (
    <Card title="Triggers" data-testid="triggers">
      <p className="mb-3 text-sm text-ink-muted">
        Another system — a monitoring tool, a form, ServiceNow — opens a work item by calling a trigger&apos;s URL with
        its token. The triggers and the tracker&apos;s start rules are part of the project&apos;s configuration; edit
        them below.
      </p>
      {listed.error ? (
        <ErrorNote>{String(listed.error)}</ErrorNote>
      ) : !listed.data ? (
        <p className="text-sm text-ink-muted">Reading…</p>
      ) : triggers.length === 0 ? (
        <p className="text-sm text-ink-muted" data-testid="triggers-none">
          No trigger declared.
        </p>
      ) : (
        <ul className="space-y-4 text-sm">
          {triggers.map((trigger) => (
            <TriggerLine key={trigger.name} slug={slug} trigger={trigger} />
          ))}
        </ul>
      )}
      <ConfigEditor slug={slug} />
    </Card>
  );
}

/** A JSON array as typed, or why it cannot be saved. */
export function parseList(text: string): { list: unknown[] } | { error: string } {
  let value: unknown;
  try {
    value = JSON.parse(text.trim() || "[]");
  } catch {
    return { error: "Not JSON" };
  }
  if (!Array.isArray(value)) return { error: "A JSON list: [ … ]" };
  if (value.some((entry) => entry === null || typeof entry !== "object" || Array.isArray(entry))) {
    return { error: "Each entry is an object: { … }" };
  }
  return { list: value };
}

const pretty = (value: unknown) => JSON.stringify(value ?? [], null, 2);

/**
 * `triggers` and `start_rules` of the project's configuration, as JSON (S51-02, ADR 0113). The API
 * validates them (a pointer, a filter, a rule without a condition) and says what it refuses; the
 * write merges into the latest configuration, so another section's change is kept.
 */
function ConfigEditor({ slug }: { slug: string }) {
  const project = useQuery({ queryKey: ["project", slug], queryFn: () => api.project(slug) });
  const config = (project.data?.config ?? {}) as { triggers?: unknown[]; start_rules?: unknown[] };
  const loaded = project.data ? `${pretty(config.triggers)}\n${pretty(config.start_rules)}` : null;
  const [seen, setSeen] = useState<string | null>(null);
  const [triggersText, setTriggersText] = useState("[]");
  const [rulesText, setRulesText] = useState("[]");
  const client = useQueryClient();
  const { patch, pending, message, error } = useProjectConfigPatch(slug);
  if (loaded !== null && loaded !== seen) {
    // The configuration as read, until the person types: a save refreshes it from the API.
    setSeen(loaded);
    setTriggersText(pretty(config.triggers));
    setRulesText(pretty(config.start_rules));
  }
  const triggersParsed = parseList(triggersText);
  const rulesParsed = parseList(rulesText);
  const ready = "list" in triggersParsed && "list" in rulesParsed;
  if (!project.data) return null;
  return (
    <form
      className="mt-4 border-t border-line pt-3"
      data-testid="triggers-editor"
      onSubmit={(event) => {
        event.preventDefault();
        if (!("list" in triggersParsed) || !("list" in rulesParsed)) return;
        const triggers = triggersParsed.list;
        const rules = rulesParsed.list;
        void patch(() => ({ triggers, start_rules: rules }) as Partial<ProjectConfigValue>, {
          saved: "Triggers and start rules saved",
        }).then((saved) => {
          if (saved) void client.invalidateQueries({ queryKey: ["triggers", slug] });
        });
      }}
    >
      <Field
        label="Triggers (JSON)"
        hint='Each: name, title, and optionally body, key, labels, fields, workflow, when, auth ("token" or "signature").'
        error={"error" in triggersParsed ? triggersParsed.error : undefined}
      >
        {(props) => (
          <Textarea
            {...props}
            rows={6}
            value={triggersText}
            onChange={(e) => setTriggersText(e.target.value)}
            className="font-mono"
          />
        )}
      </Field>
      <Field
        className="mt-3"
        label="Tracker start rules (JSON)"
        hint="A tracker item matching a rule starts without the agent-ready label: labels_any, labels_all, item_type, states."
        error={"error" in rulesParsed ? rulesParsed.error : undefined}
      >
        {(props) => (
          <Textarea {...props} rows={4} value={rulesText} onChange={(e) => setRulesText(e.target.value)} className="font-mono" />
        )}
      </Field>
      <div className="mt-3 flex flex-wrap items-center justify-end gap-3">
        {message && <span className="text-sm text-ink-muted">{message}</span>}
        {error && <ErrorNote>{error}</ErrorNote>}
        <Button type="submit" size="sm" disabled={!ready || pending}>
          Save
        </Button>
      </div>
    </form>
  );
}

function TriggerLine({ slug, trigger }: { slug: string; trigger: Trigger }) {
  const client = useQueryClient();
  const [minted, setMinted] = useState<TriggerToken | null>(null);
  const [payload, setPayload] = useState("{}");
  const [fired, setFired] = useState<TriggerAck | null>(null);
  const [invalid, setInvalid] = useState<string | null>(null);
  const mint = useMutation({
    mutationFn: () => api.rotateTriggerToken(slug, trigger.name),
    onSuccess: (token) => {
      setMinted(token);
      void client.invalidateQueries({ queryKey: ["triggers", slug] });
    },
  });
  const fire = useMutation({
    mutationFn: (body: Record<string, unknown>) => api.fireTrigger(slug, trigger.name, body),
    onSuccess: (ack) => {
      setFired(ack);
      void client.invalidateQueries({ queryKey: ["received-events", slug] });
    },
  });
  const onFire = () => {
    let body: unknown;
    try {
      body = JSON.parse(payload);
    } catch {
      setInvalid("not JSON");
      return;
    }
    if (body === null || typeof body !== "object" || Array.isArray(body)) {
      setInvalid("a JSON object");
      return;
    }
    setInvalid(null);
    fire.mutate(body as Record<string, unknown>);
  };
  return (
    <li className="border-t border-line pt-3" data-testid={`trigger-${trigger.name}`}>
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="font-medium">{trigger.name}</span>
        <span className="text-ink-muted">→ {trigger.workflow ? `workflow ${trigger.workflow}` : "the project's routing"}</span>
        {trigger.auth === "signature" && <Badge tone="neutral">signed</Badge>}
        <Badge tone={trigger.has_token ? "ok" : "warn"}>{trigger.has_token ? "token set" : "no token yet"}</Badge>
      </div>
      <code className="mt-1 block break-all text-xs" data-testid={`trigger-url-${trigger.name}`}>
        POST {trigger.url}
      </code>
      <div className="mt-2 flex flex-wrap gap-2">
        <Button size="sm" onClick={() => mint.mutate()} disabled={mint.isPending}>
          {trigger.has_token ? "Mint a new token" : "Mint the token"}
        </Button>
      </div>
      {mint.error && <ErrorNote>{String(mint.error)}</ErrorNote>}
      {minted && (
        <div className="mt-2 rounded bg-surface-sunken p-2 text-xs" data-testid={`trigger-token-${trigger.name}`}>
          <p className="mb-1 text-ink-muted">Shown once: copy it now. The previous token no longer works.</p>
          <p className="break-all">
            Token: <code>{minted.token}</code>
          </p>
          {trigger.auth === "signature" && (
            <p className="break-all">
              Signing secret (HMAC-SHA256 of the body, in <code>X-Choregos-Signature: sha256=…</code>):{" "}
              <code>{minted.signing_secret}</code>
            </p>
          )}
        </div>
      )}
      <div className="mt-3">
        <Field label="Fire by hand with a payload" error={invalid ? `The payload must be ${invalid}.` : undefined}>
          {(props) => (
            <Textarea {...props} rows={3} value={payload} onChange={(e) => setPayload(e.target.value)} className="font-mono" />
          )}
        </Field>
        <div className="mt-2">
          <Button size="sm" tone="accent" onClick={onFire} disabled={fire.isPending}>
            Fire
          </Button>
        </div>
        {fire.error && <ErrorNote>{String(fire.error)}</ErrorNote>}
        {fired && (
          <p className="mt-1 text-xs" data-testid={`trigger-fired-${trigger.name}`}>
            {OUTCOME[fired.outcome ?? ""] ?? (fired.duplicate ? "already received" : fired.outcome)}
            {fired.work_item_key ? ` — ${fired.work_item_key}` : ""}
            {fired.workflow ? ` in ${fired.workflow}` : ""}
          </p>
        )}
      </div>
    </li>
  );
}
