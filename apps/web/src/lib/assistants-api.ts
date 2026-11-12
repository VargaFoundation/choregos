// SPDX-License-Identifier: Apache-2.0
/**
 * The client of the assistants (stream S63, ADR 0133-0140): what a person reads and does with the
 * assistants of their audience, and what the owners do to an installation. Typed by the generated
 * contracts; the streamed turn is read here too, since `EventSource` cannot POST.
 */
import type {
  Assistant,
  AssistantBind,
  AssistantInstall,
  AssistantList,
  AssistantReadPage,
  AssistantReadiness,
  AssistantStopResult,
  AssistantUpdate,
  ConnectedAccountConnect,
  ConnectedAccountList,
  Conversation,
  ConversationAction,
  ConversationActionList,
  ConversationCandidate,
  ConversationCandidateAccept,
  ConversationFeedback,
  ConversationFeedbackCreate,
  ConversationPage,
  ConversationSummary,
  Delegation,
  DelegationCreate,
  DelegationList,
  InboxList,
  KnowledgeUpdate,
  KnowledgeUpdateDecision,
  KnowledgeUpdateList,
  MemoryAction,
  MemoryExport,
  MemorySpace,
  MyAssistantList,
  PluginAssistantList,
  SubjectErasure,
  StopRequest,
  SubjectErasureRequest,
  TaskEventKey,
  TaskEventList,
  TaskRun,
  TaskSubscription,
  TaskSubscriptionPut,
  TeamsDelivery,
  UserErasure,
  WebhookSecret,
} from "@choregos/contracts/api";
import { API_BASE, ApiError, problemOf, request } from "./api";

export type {
  Assistant,
  AssistantBinding,
  AssistantReadLine,
  AssistantReadinessItem,
  AssistantStop,
  AssistantStopResult,
  ConnectedAccount,
  Conversation,
  ConversationAction,
  ConversationCandidate,
  ConversationMessage,
  ConversationSummary,
  Delegation,
  InboxItem,
  KnowledgeUpdate,
  MemoryFactView,
  MemoryProfileVersion,
  MemorySpace,
  MyAssistant,
  PluginAssistant,
  SubjectErasure,
  TaskEventLine,
  TaskSubscription,
  AssistantReadiness,
  AssistantReadPage,
} from "@choregos/contracts/api";

const enc = encodeURIComponent;

function query(params: Record<string, string | undefined>): string {
  const entries = Object.entries(params).filter((e): e is [string, string] => !!e[1]);
  return entries.length ? `?${new URLSearchParams(entries)}` : "";
}

const post = (body?: unknown): RequestInit => ({
  method: "POST",
  body: body === undefined ? undefined : JSON.stringify(body),
});

/** What the streamed turn tells its reader. */
export interface TurnHandlers {
  onDelta: (text: string) => void;
  onDone: (answer: { status: string; output_text?: string; missing_accounts?: MissingAccount[] }) => void;
  onError: (message: string) => void;
}

export interface MissingAccount {
  need?: string;
  connect_url?: string;
}

/** One server-sent event of the turn, as `event:` and `data:` lines. */
export function parseEvents(buffer: string): { events: { event: string; data: string }[]; rest: string } {
  const events: { event: string; data: string }[] = [];
  const blocks = buffer.split(/\r?\n\r?\n/);
  const rest = blocks.pop() ?? "";
  for (const block of blocks) {
    let event = "message";
    const data: string[] = [];
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
    }
    if (data.length) events.push({ event, data: data.join("\n") });
  }
  return { events, rest };
}

/** Sends one turn and reads the answer as it streams: `response.output_text.delta` chunks, then the end. */
export async function streamTurn(
  conversationId: string,
  input: string,
  handlers: TurnHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_BASE}/me/conversations/${enc(conversationId)}/messages`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ input, stream: true }),
    signal,
  });
  if (!response.ok) {
    throw new ApiError(response.status, await problemOf(response));
  }
  const reader = response.body?.getReader();
  if (!reader) {
    handlers.onError("the answer could not be read");
    return;
  }
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parsed = parseEvents(buffer);
    buffer = parsed.rest;
    for (const { event, data } of parsed.events) {
      try {
        const payload = JSON.parse(data) as Record<string, unknown>;
        if (event === "response.output_text.delta") handlers.onDelta(String(payload.delta ?? ""));
        else if (event === "error") handlers.onError(String(payload.detail ?? "the turn failed"));
        else if (event.startsWith("response.") && event !== "response.created") {
          handlers.onDone(payload as Parameters<TurnHandlers["onDone"]>[0]);
        }
      } catch {
        // an unreadable event must not break the stream
      }
    }
  }
}

export const assistantsApi = {
  // A person's assistants.
  myAssistants: () => request<MyAssistantList>("/me/assistants"),
  openConversation: (org: string, slug: string, context?: Record<string, unknown>) =>
    request<Conversation>(`/me/assistants/${enc(org)}/${enc(slug)}/conversations`, post({ context: context ?? null })),
  eraseMyData: (org: string, slug: string) =>
    request<UserErasure>(`/me/assistants/${enc(org)}/${enc(slug)}`, { method: "DELETE" }),
  // Conversations.
  myConversations: () => request<ConversationPage>("/me/conversations"),
  conversation: (id: string) => request<Conversation>(`/me/conversations/${enc(id)}`),
  deleteConversation: (id: string) => request<void>(`/me/conversations/${enc(id)}`, { method: "DELETE" }),
  cancelTurn: (id: string) => request<ConversationSummary>(`/me/conversations/${enc(id)}/cancel`, post()),
  conversationActions: (id: string) => request<ConversationActionList>(`/me/conversations/${enc(id)}/actions`),
  confirmAction: (id: string, action: string) =>
    request<ConversationAction>(`/me/conversations/${enc(id)}/actions/${enc(action)}/confirm`, post()),
  refuseAction: (id: string, action: string) =>
    request<ConversationAction>(`/me/conversations/${enc(id)}/actions/${enc(action)}/refuse`, post()),
  feedback: (org: string, session: string, body: ConversationFeedbackCreate) =>
    request<ConversationFeedback>(`/orgs/${enc(org)}/agent-sessions/${enc(session)}/feedback`, post(body)),
  // Memory.
  memory: (org: string, slug: string, space: string, subject?: string) =>
    subject
      ? request<MemorySpace>(`/me/assistants/${enc(org)}/${enc(slug)}/memory/${enc(space)}/${enc(subject)}`)
      : request<MemorySpace>(`/me/assistants/${enc(org)}/${enc(slug)}/memory/${enc(space)}`),
  memoryAction: (org: string, slug: string, space: string, body: MemoryAction, subject?: string) =>
    subject
      ? request<MemorySpace>(`/me/assistants/${enc(org)}/${enc(slug)}/memory/${enc(space)}/${enc(subject)}`, {
          method: "PATCH",
          body: JSON.stringify(body),
        })
      : request<MemorySpace>(`/me/assistants/${enc(org)}/${enc(slug)}/memory/${enc(space)}`, {
          method: "PATCH",
          body: JSON.stringify(body),
        }),
  deleteMemory: (org: string, slug: string, space: string, factId?: string, subject?: string) =>
    subject
      ? request<void>(
          `/me/assistants/${enc(org)}/${enc(slug)}/memory/${enc(space)}/${enc(subject)}${query({ fact_id: factId })}`,
          { method: "DELETE" },
        )
      : request<void>(`/me/assistants/${enc(org)}/${enc(slug)}/memory/${enc(space)}${query({ fact_id: factId })}`, {
          method: "DELETE",
        }),
  exportMemory: (org: string, slug: string) =>
    request<MemoryExport>(`/me/assistants/${enc(org)}/${enc(slug)}/memory-export`),
  // The installation's own memory, for its owners.
  installationMemory: (org: string, slug: string, space: string) =>
    request<MemorySpace>(`/orgs/${enc(org)}/assistants/${enc(slug)}/memory/${enc(space)}`),
  installationMemoryAction: (org: string, slug: string, space: string, body: MemoryAction) =>
    request<MemorySpace>(`/orgs/${enc(org)}/assistants/${enc(slug)}/memory/${enc(space)}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  // Connected accounts.
  connectedAccounts: () => request<ConnectedAccountList>("/me/connected-accounts"),
  connectAccount: (connector: string, org: string) =>
    request<ConnectedAccountConnect>(`/me/connected-accounts/${enc(connector)}/connect${query({ org })}`, post()),
  disconnectAccount: (connector: string, org: string) =>
    request<void>(`/me/connected-accounts/${enc(connector)}${query({ org })}`, { method: "DELETE" }),
  // Tasks and inbox.
  task: (org: string, slug: string, task: string) =>
    request<TaskSubscription>(`/me/assistants/${enc(org)}/${enc(slug)}/tasks/${enc(task)}`),
  putTask: (org: string, slug: string, task: string, body: TaskSubscriptionPut) =>
    request<TaskSubscription>(`/me/assistants/${enc(org)}/${enc(slug)}/tasks/${enc(task)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  deleteTask: (org: string, slug: string, task: string) =>
    request<void>(`/me/assistants/${enc(org)}/${enc(slug)}/tasks/${enc(task)}`, { method: "DELETE" }),
  runTask: (org: string, slug: string, task: string) =>
    request<TaskRun>(`/me/assistants/${enc(org)}/${enc(slug)}/tasks/${enc(task)}/run`, post()),
  inbox: () => request<InboxList>("/me/inbox"),
  // Standing delegations of a task (S63-14): agree to the exact needs, list, revoke.
  delegations: () => request<DelegationList>("/me/delegations"),
  agreeDelegation: (body: DelegationCreate) => request<Delegation>("/me/delegations", post(body)),
  revokeDelegation: (id: string) => request<void>(`/me/delegations/${enc(id)}`, { method: "DELETE" }),

  // The owners.
  pluginAssistants: (org: string, plugin: string) =>
    request<PluginAssistantList>(`/orgs/${enc(org)}/plugins/${enc(plugin)}/assistants`),
  installations: (org: string) => request<AssistantList>(`/orgs/${enc(org)}/assistants`),
  installation: (org: string, slug: string) => request<Assistant>(`/orgs/${enc(org)}/assistants/${enc(slug)}`),
  install: (org: string, body: AssistantInstall) =>
    request<Assistant>(`/orgs/${enc(org)}/assistants`, post(body)),
  update: (org: string, slug: string, body: AssistantUpdate) =>
    request<Assistant>(`/orgs/${enc(org)}/assistants/${enc(slug)}`, { method: "PATCH", body: JSON.stringify(body) }),
  uninstall: (org: string, slug: string) =>
    request<void>(`/orgs/${enc(org)}/assistants/${enc(slug)}`, { method: "DELETE" }),
  readiness: (org: string, slug: string) =>
    request<AssistantReadiness>(`/orgs/${enc(org)}/assistants/${enc(slug)}/readiness`),
  /** Stops the assistant and the turns its conversations are taking, with a reason (ADR 0142 §4 bis). */
  stop: (org: string, slug: string, reason: string) =>
    request<AssistantStopResult>(`/orgs/${enc(org)}/assistants/${enc(slug)}/stop`, post({ reason } satisfies StopRequest)),
  /** Clears the stop: the status is derived again, and nothing stopped restarts on its own. */
  resume: (org: string, slug: string) => request<Assistant>(`/orgs/${enc(org)}/assistants/${enc(slug)}/resume`, post()),
  bind: (org: string, slug: string, need: string, body: AssistantBind) =>
    request<Assistant>(`/orgs/${enc(org)}/assistants/${enc(slug)}/bindings/${enc(need)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  knowledgeUpdates: (org: string, slug: string, status?: string) =>
    request<KnowledgeUpdateList>(`/orgs/${enc(org)}/assistants/${enc(slug)}/knowledge/updates${query({ status })}`),
  decideKnowledgeUpdate: (org: string, slug: string, id: string, body: KnowledgeUpdateDecision) =>
    request<KnowledgeUpdate>(`/orgs/${enc(org)}/assistants/${enc(slug)}/knowledge/updates/${enc(id)}`, post(body)),
  reads: (org: string, slug: string, filter: { subject_type?: string; subject_key?: string; user_id?: string }) =>
    request<AssistantReadPage>(`/orgs/${enc(org)}/assistants/${enc(slug)}/reads${query(filter)}`),
  eraseSubject: (org: string, slug: string, body: SubjectErasureRequest) =>
    request<SubjectErasure>(`/orgs/${enc(org)}/assistants/${enc(slug)}/erasure`, post(body)),
  webhookSecret: (org: string, slug: string, rotate = false) =>
    request<WebhookSecret>(`/orgs/${enc(org)}/assistants/${enc(slug)}/webhook-secret`, post({ rotate })),
  // The door of a `trigger` task and its journal (S63-21), the Teams channel of the deliveries (S63-25).
  taskEventKey: (org: string, slug: string, task: string) =>
    request<TaskEventKey>(`/orgs/${enc(org)}/assistants/${enc(slug)}/tasks/${enc(task)}/event-key`, post()),
  taskEvents: (org: string, slug: string) =>
    request<TaskEventList>(`/orgs/${enc(org)}/assistants/${enc(slug)}/task-events`),
  teamsDelivery: (org: string, slug: string) =>
    request<TeamsDelivery>(`/orgs/${enc(org)}/assistants/${enc(slug)}/delivery/teams`),
  putTeamsDelivery: (org: string, slug: string, webhook_url: string) =>
    request<TeamsDelivery>(`/orgs/${enc(org)}/assistants/${enc(slug)}/delivery/teams`, {
      method: "PUT",
      body: JSON.stringify({ webhook_url }),
    }),
  deleteTeamsDelivery: (org: string, slug: string) =>
    request<void>(`/orgs/${enc(org)}/assistants/${enc(slug)}/delivery/teams`, { method: "DELETE" }),
  feedbackCandidates: (org: string, agent: string) =>
    request<ConversationCandidate[]>(`/orgs/${enc(org)}/agents/${enc(agent)}/feedback-candidates`),
  acceptCandidate: (org: string, agent: string, id: string, body: ConversationCandidateAccept) =>
    request<ConversationCandidate>(
      `/orgs/${enc(org)}/agents/${enc(agent)}/feedback-candidates/${enc(id)}/accept`,
      post(body),
    ),
  dismissCandidate: (org: string, agent: string, id: string) =>
    request<ConversationCandidate>(
      `/orgs/${enc(org)}/agents/${enc(agent)}/feedback-candidates/${enc(id)}/dismiss`,
      post(),
    ),
};
