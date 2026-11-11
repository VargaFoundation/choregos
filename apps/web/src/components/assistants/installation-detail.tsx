// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { groupsOf } from "@/components/assistants/install-assistant";
import { ConfirmedGesture } from "@/components/confirmed-gesture";
import { OrgPageHeader } from "@/components/org-page-header";
import { SchemaForm, type JsonSchema } from "@/components/schema-form";
import {
  Badge,
  Button,
  Card,
  Empty,
  ErrorNote,
  Field,
  Input,
  ReadState,
  Select,
  Table,
  TBody,
  TD,
  TH,
  THead,
  TR,
  Textarea,
  buttonClasses,
} from "@/components/ui";
import { api } from "@/lib/api";
import { assistantsApi, type Assistant, type KnowledgeUpdate, type SubjectErasure } from "@/lib/assistants-api";
import { shortDate } from "@/lib/format";
import { useSession } from "@/lib/session";

const STATUS_TONE = { ready: "succeeded", incomplete: "waiting", suspended: "neutral" } as const;

type Ref = { org: string; slug: string };

/** Each need of the installation, the connector it is bound to, and what still blocks it. */
function Bindings({ org, slug, assistant, needs }: Ref & { assistant: Assistant; needs: { need: string; required: boolean; access: string }[] }) {
  const client = useQueryClient();
  const readiness = useQuery({ queryKey: ["readiness", org, slug], queryFn: () => assistantsApi.readiness(org, slug) });
  const connectors = useQuery({ queryKey: ["org-connectors", org], queryFn: () => api.orgConnectors(org) });
  const [error, setError] = useState<string | null>(null);
  async function bind(need: string, connector: string, approval?: "inline" | "console") {
    setError(null);
    try {
      await assistantsApi.bind(org, slug, need, { connector, enabled: true, approval: approval ?? null });
      await Promise.all([
        client.invalidateQueries({ queryKey: ["installation", org, slug] }),
        client.invalidateQueries({ queryKey: ["readiness", org, slug] }),
      ]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Binding refused");
    }
  }
  return (
    <Card title="Bindings and readiness">
      <div className="space-y-4">
        <ReadState lecture={readiness} quoi="the readiness" />
        {readiness.data && readiness.data.items.length === 0 && <Badge tone="succeeded">Nothing missing</Badge>}
        <ul className="space-y-1 text-sm">
          {readiness.data?.items.map((item, index) => (
            <li key={index} className="flex flex-wrap items-center gap-2">
              <Badge tone="waiting">{item.kind.replaceAll("_", " ")}</Badge>
              <span>{item.message}</span>
              <span className="text-xs text-ink-muted">to fix: {item.fix_by === "org_admin" ? "an administrator" : "the owners"}</span>
            </li>
          ))}
        </ul>
        {error && <ErrorNote>{error}</ErrorNote>}
        <div className="space-y-2">
          {needs.map((n) => {
            const bound = assistant.bindings.find((b) => b.need === n.need);
            return (
              <div key={n.need} className="flex flex-wrap items-center gap-3">
                <span className="w-40 text-sm font-medium">
                  {n.need} {!n.required && <span className="text-xs text-ink-muted">(optional)</span>}
                </span>
                <Select
                  aria-label={`Connector for ${n.need}`}
                  value={bound?.connector ?? ""}
                  onChange={(e) => e.target.value && void bind(n.need, e.target.value, bound?.approval === "inline" ? "inline" : undefined)}
                >
                  <option value="">Not bound</option>
                  {(connectors.data ?? []).map((c) => (
                    <option key={c.name} value={c.name}>
                      {c.name}
                    </option>
                  ))}
                </Select>
                {bound && n.access === "write" && (
                  <Select
                    aria-label={`Approval for ${n.need}`}
                    value={bound.approval === "inline" ? "inline" : "console"}
                    onChange={(e) => void bind(n.need, bound.connector, e.target.value as "inline" | "console")}
                  >
                    <option value="console">Approve in the console</option>
                    <option value="inline">Approve in the conversation</option>
                  </Select>
                )}
                {bound && <Badge tone="info">{bound.identity.replaceAll("_", " ")}</Badge>}
              </div>
            );
          })}
        </div>
      </div>
    </Card>
  );
}

/** Configuration, audience, owners, channel and budgets. */
function Settings({ org, slug, assistant, schema }: Ref & { assistant: Assistant; schema: JsonSchema | null }) {
  const client = useQueryClient();
  const budget = (assistant.policy_overrides?.budget ?? {}) as Record<string, number>;
  const [config, setConfig] = useState<Record<string, unknown>>(assistant.config);
  const [audience, setAudience] = useState(assistant.audience_groups.join(", "));
  const [owners, setOwners] = useState(assistant.owner_groups.join(", "));
  const [channel, setChannel] = useState(assistant.channel);
  const [caps, setCaps] = useState({
    per_user_month_usd: budget.per_user_month_usd?.toString() ?? "",
    per_conversation_usd: budget.per_conversation_usd?.toString() ?? "",
    per_installation_month_usd: budget.per_installation_month_usd?.toString() ?? "",
  });
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  async function save() {
    setError(null);
    setSaved(false);
    const limits = Object.fromEntries(
      Object.entries(caps)
        .filter(([, v]) => v.trim() !== "")
        .map(([k, v]) => [k, Number(v)]),
    );
    try {
      await assistantsApi.update(org, slug, {
        config,
        audience_groups: groupsOf(audience),
        owner_groups: groupsOf(owners),
        channel,
        policy_overrides: { ...assistant.policy_overrides, budget: limits },
      });
      await client.invalidateQueries({ queryKey: ["installation", org, slug] });
      setSaved(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Update refused");
    }
  }
  return (
    <Card title="Settings">
      <div className="space-y-4">
        {schema && <SchemaForm schema={schema} value={config} onChange={setConfig} org={org} />}
        <div className="grid gap-3 md:grid-cols-3">
          <Field label="Audience groups">{(p) => <Input {...p} value={audience} onChange={(e) => setAudience(e.target.value)} />}</Field>
          <Field label="Owner groups" hint="Changing owners is the administrator's.">
            {(p) => <Input {...p} value={owners} onChange={(e) => setOwners(e.target.value)} />}
          </Field>
          <Field label="Channel">
            {(p) => (
              <Select {...p} value={channel} onChange={(e) => setChannel(e.target.value as typeof channel)}>
                <option value="stable">Stable</option>
                <option value="candidate">Candidate</option>
              </Select>
            )}
          </Field>
        </div>
        <div className="grid gap-3 md:grid-cols-3">
          {(
            [
              ["per_user_month_usd", "Per user, per month (USD)"],
              ["per_conversation_usd", "Per conversation (USD)"],
              ["per_installation_month_usd", "Whole installation, per month (USD)"],
            ] as const
          ).map(([key, label]) => (
            <Field key={key} label={label}>
              {(p) => <Input {...p} inputMode="decimal" value={caps[key]} onChange={(e) => setCaps({ ...caps, [key]: e.target.value })} />}
            </Field>
          ))}
        </div>
        {error && <ErrorNote>{error}</ErrorNote>}
        {saved && <p className="text-sm text-ink-muted">Saved.</p>}
        <Button tone="primary" onClick={() => void save()}>
          Save settings
        </Button>
      </div>
    </Card>
  );
}

/** The key the task webhook is signed with: shown once; a second showing needs a rotation. */
function WebhookKey({ org, slug }: Ref) {
  const [shown, setShown] = useState<{ secret: string; generation: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function reveal(rotate: boolean) {
    setError(null);
    try {
      const got = await assistantsApi.webhookSecret(org, slug, rotate);
      setShown({ secret: got.secret, generation: got.generation });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Refused");
    }
  }
  return (
    <Card title="Task webhook key" eyebrow="Shown once">
      <div className="space-y-3">
        <p className="text-sm text-ink-muted">
          Deliveries to the task webhook are signed with this key (HMAC-SHA256 in <code>X-Choregos-Signature</code>).
          Copy it now: it is shown once, and a new one needs a rotation.
        </p>
        {shown && (
          <p className="text-sm">
            Key (generation {shown.generation}): <code data-verbatim>{shown.secret}</code>
          </p>
        )}
        {error && <ErrorNote>{error}</ErrorNote>}
        <div className="flex flex-wrap gap-2">
          <Button size="sm" tone="default" onClick={() => void reveal(false)}>
            Show the key
          </Button>
          <Button size="sm" tone="danger" onClick={() => void reveal(true)}>
            Rotate the key
          </Button>
        </div>
      </div>
    </Card>
  );
}

function DiffLines({ diff }: { diff: string }) {
  return (
    <pre className="overflow-x-auto rounded bg-surface-muted p-2 text-xs" aria-label="Diff">
      {diff.split("\n").map((line, index) => (
        <div key={index} className={line.startsWith("+") ? "bg-succeeded-soft" : line.startsWith("-") ? "bg-failed-soft" : ""}>
          {line || " "}
        </div>
      ))}
    </pre>
  );
}

function UpdateCard({ org, slug, update }: Ref & { update: KnowledgeUpdate }) {
  const client = useQueryClient();
  const [merged, setMerged] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function decide(decision: "accept" | "refuse" | "merge") {
    setError(null);
    try {
      await assistantsApi.decideKnowledgeUpdate(org, slug, update.id, {
        decision,
        content: decision === "merge" ? (merged ?? update.proposed_content ?? "") : null,
      });
      await client.invalidateQueries({ queryKey: ["knowledge-updates", org, slug] });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Decision refused");
    }
  }
  return (
    <Card padding="sm" title={update.path} verbatimTitle eyebrow={`${update.kind} · ${update.base} · plugin ${update.plugin_version}`}>
      <div className="space-y-2">
        <DiffLines diff={update.diff} />
        {merged !== null && <Textarea aria-label="Merged content" value={merged} onChange={(e) => setMerged(e.target.value)} />}
        {error && <ErrorNote>{error}</ErrorNote>}
        <div className="flex flex-wrap gap-2">
          <Button size="sm" tone="primary" onClick={() => void decide("accept")}>
            Accept
          </Button>
          {merged === null ? (
            <Button size="sm" tone="default" onClick={() => setMerged(update.proposed_content ?? "")}>
              Merge with edits
            </Button>
          ) : (
            <Button size="sm" tone="default" onClick={() => void decide("merge")}>
              Save the merge
            </Button>
          )}
          <Button size="sm" tone="danger" onClick={() => void decide("refuse")}>
            Refuse
          </Button>
        </div>
      </div>
    </Card>
  );
}

function KnowledgeUpdates({ org, slug }: Ref) {
  const updates = useQuery({
    queryKey: ["knowledge-updates", org, slug],
    queryFn: () => assistantsApi.knowledgeUpdates(org, slug, "proposed"),
  });
  const items = updates.data?.items ?? [];
  return (
    <Card title="Knowledge updates" eyebrow="Proposed by the plugin">
      <div className="space-y-3">
        <ReadState lecture={updates} quoi="the knowledge updates" />
        {updates.data && items.length === 0 && <Empty>No update waits for a decision.</Empty>}
        {items.map((u) => (
          <UpdateCard key={u.id} org={org} slug={slug} update={u} />
        ))}
      </div>
    </Card>
  );
}

/** The reads journal, for auditors: one line per tool call of the assistant's conversations. */
function ReadsJournal({ org, slug }: Ref) {
  const [filter, setFilter] = useState({ subject_type: "", subject_key: "", user_id: "" });
  const [applied, setApplied] = useState(filter);
  const reads = useQuery({
    queryKey: ["assistant-reads", org, slug, applied],
    queryFn: () => assistantsApi.reads(org, slug, applied),
  });
  return (
    <Card title="Reads journal" eyebrow="Auditors">
      <div className="space-y-3">
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            setApplied(filter);
          }}
        >
          {(["subject_type", "subject_key", "user_id"] as const).map((key) => (
            <label key={key} className="space-y-1 text-xs text-ink-muted">
              {key.replaceAll("_", " ")}
              <Input value={filter[key]} onChange={(e) => setFilter({ ...filter, [key]: e.target.value })} />
            </label>
          ))}
          <Button type="submit" size="sm">
            Filter
          </Button>
        </form>
        <ReadState lecture={reads} quoi="the reads journal" />
        {reads.data && reads.data.items.length === 0 && <Empty>No read recorded.</Empty>}
        {reads.data && reads.data.items.length > 0 && (
          <Table>
            <THead>
              <TR>
                <TH>When</TH>
                <TH>User</TH>
                <TH>Need</TH>
                <TH>Operation</TH>
                <TH>Subject</TH>
              </TR>
            </THead>
            <TBody>
              {reads.data.items.map((line) => (
                <TR key={line.id}>
                  <TD>{shortDate(line.at)}</TD>
                  <TD>{line.user_id}</TD>
                  <TD>{line.need}</TD>
                  <TD>{line.operation}</TD>
                  <TD>{line.subject_type ? `${line.subject_type}: ${line.subject_key}` : "—"}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </div>
    </Card>
  );
}

/** Export or erase what the assistant holds about one subject. */
function SubjectRights({ org, slug }: Ref) {
  const [type, setType] = useState("");
  const [key, setKey] = useState("");
  const [conversations, setConversations] = useState<"redact" | "delete">("redact");
  const [result, setResult] = useState<SubjectErasure | null>(null);
  const [error, setError] = useState<string | null>(null);
  const subject = { type: type.trim(), key: key.trim() };
  async function run(mode: "export" | "erase") {
    setError(null);
    try {
      setResult(await assistantsApi.eraseSubject(org, slug, { subject, mode, conversations }));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Refused");
      throw cause;
    }
  }
  const ready = subject.type !== "" && subject.key !== "";
  return (
    <Card title="Export or erase by subject" eyebrow="Auditors">
      <div className="space-y-3">
        <div className="flex flex-wrap items-end gap-2">
          <label className="space-y-1 text-xs text-ink-muted">
            Subject type
            <Input value={type} onChange={(e) => setType(e.target.value)} placeholder="instrument" />
          </label>
          <label className="space-y-1 text-xs text-ink-muted">
            Subject key
            <Input value={key} onChange={(e) => setKey(e.target.value)} />
          </label>
          <label className="space-y-1 text-xs text-ink-muted">
            Conversations on erasure
            <Select value={conversations} onChange={(e) => setConversations(e.target.value as typeof conversations)}>
              <option value="redact">Redact</option>
              <option value="delete">Delete</option>
            </Select>
          </label>
          <Button size="sm" disabled={!ready} onClick={() => void run("export").catch(() => undefined)}>
            Export
          </Button>
          {ready && (
            <ConfirmedGesture
              question={`Erase everything about ${subject.type} ${subject.key}? This cannot be undone.`}
              confirmer="Erase"
              action={() => run("erase")}
            >
              Erase
            </ConfirmedGesture>
          )}
        </div>
        {error && <ErrorNote>{error}</ErrorNote>}
        {result && (
          <div className="space-y-1 text-sm">
            {result.mode === "erase" ? (
              <p>
                Erased {result.memories_erased ?? 0} memories; redacted {result.conversations_redacted ?? 0} and deleted{" "}
                {result.conversations_deleted ?? 0} conversations.
              </p>
            ) : (
              <pre className="overflow-x-auto rounded bg-surface-muted p-2 text-xs" aria-label="Export">
                {JSON.stringify(result, null, 2)}
              </pre>
            )}
          </div>
        )}
      </div>
    </Card>
  );
}

/** The candidate cases that thumbs down proposed to an agent's owners. */
function FeedbackCandidates({ org }: { org: string }) {
  const agents = useQuery({ queryKey: ["agents", org], queryFn: () => api.agents(org) });
  const [agent, setAgent] = useState("");
  const candidates = useQuery({
    queryKey: ["feedback-candidates", org, agent],
    queryFn: () => assistantsApi.feedbackCandidates(org, agent),
    enabled: agent !== "",
  });
  return (
    <Card title="Feedback candidates" eyebrow="Thumbs down">
      <div className="space-y-3">
        <Select aria-label="Agent" value={agent} onChange={(e) => setAgent(e.target.value)}>
          <option value="">Choose the assistant&apos;s agent</option>
          {(agents.data ?? []).map((a) => (
            <option key={a.slug} value={a.slug}>
              {a.slug}
            </option>
          ))}
        </Select>
        {candidates.data && candidates.data.length === 0 && <Empty>No candidate case.</Empty>}
        {(candidates.data ?? []).map((c) => (
          <Card key={c.id} padding="sm" eyebrow={`version ${c.agent_version} · ${shortDate(c.created_at)}`}>
            <pre className="overflow-x-auto text-xs">{JSON.stringify(c.candidate, null, 2)}</pre>
          </Card>
        ))}
      </div>
    </Card>
  );
}

/** The installation page of one assistant, for its owners. */
export function InstallationDetail({ slug }: { slug: string }) {
  const { org, me } = useSession();
  const router = useRouter();
  const client = useQueryClient();
  const assistant = useQuery({ queryKey: ["installation", org, slug], queryFn: () => assistantsApi.installation(org, slug) });
  const data = assistant.data;
  const shipped = useQuery({
    queryKey: ["plugin-assistants", org, data?.plugin],
    queryFn: () => assistantsApi.pluginAssistants(org, data?.plugin ?? ""),
    enabled: !!data,
  });
  const manifest = shipped.data?.items.find((a) => a.name === data?.assistant_name);
  const admin = (me?.memberships ?? []).some((m) => m.org === org && !m.project_slug && m.role === "org_admin");
  return (
    <div className="space-y-6">
      <OrgPageHeader
        title={data?.display_name ?? data?.assistant_name ?? slug}
        eyebrow="Installation"
        description={data ? `${data.plugin} ${data.plugin_version}` : undefined}
        actions={
          <>
            {data && <Badge tone={STATUS_TONE[data.status]}>{data.status}</Badge>}
            <Link href="/assistants/manage" className={buttonClasses("secondary")}>
              All installations
            </Link>
          </>
        }
      />
      <ReadState lecture={assistant} quoi="this installation" />
      {data && (
        <>
          <Settings key={data.created_at + JSON.stringify(data.config)} org={org} slug={slug} assistant={data} schema={(manifest?.config_schema ?? null) as JsonSchema | null} />
          <Bindings org={org} slug={slug} assistant={data} needs={manifest?.needs ?? []} />
          <WebhookKey org={org} slug={slug} />
          <KnowledgeUpdates org={org} slug={slug} />
          <ReadsJournal org={org} slug={slug} />
          <SubjectRights org={org} slug={slug} />
          <FeedbackCandidates org={org} />
          {admin && (
            <ConfirmedGesture
              question="Uninstall this assistant? Its conversations and memories stay until their retention ends."
              confirmer="Uninstall"
              action={async () => {
                await assistantsApi.uninstall(org, slug);
                await client.invalidateQueries({ queryKey: ["installations", org] });
                router.push("/assistants/manage");
              }}
            >
              Uninstall
            </ConfirmedGesture>
          )}
        </>
      )}
    </div>
  );
}
