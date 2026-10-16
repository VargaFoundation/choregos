import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ChannelBadges, RevisionChannels } from "@/components/agents/channels";
import { publishedSentence } from "@/components/agents/builder";
import { canarySentence, sourceLabel } from "@/components/agents/registre";
import {
  LifecycleTab,
  PendingRelease,
  ReleasePipeline,
  blockers,
  rollbackTargets,
  unreleasedRevision,
} from "@/components/agents/releases";
import { api } from "@/lib/api";
import type { Agent, AgentRelease, AgentReleaseCheck, AgentReleaseReadiness } from "@/lib/types";

/**
 * The lifecycle tab (S40-02c): production, testing, the revision to send, the checklist before a release,
 * rollback, approvals. The server's words (`can.release`, `cannot_reason`, the checks) are shown as they are.
 */
const session = vi.hoisted(() => ({ email: "lea@acme.dev" }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme", me: { email: session.email, memberships: [] } }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      agentExposure: vi.fn(),
      agentMetrics: vi.fn(),
      agentReleases: vi.fn(),
      agentReleaseReadiness: vi.fn(),
      releaseAgentVersion: vi.fn(),
      decideAgentRelease: vi.fn(),
      setAgentReleasePolicy: vi.fn(),
    },
  };
});

const SPEC = { instructions: "Review." };
const agent = (over: Partial<Agent> = {}): Agent => ({
  slug: "reviewer",
  kind: "internal",
  display_name: "Reviewer",
  status: "active",
  latest_version: 3,
  lifecycle: "active",
  scope: "org",
  can: { publish: true, manage: true, pin: true, release: true, fork_scopes: [] },
  stable_version: 1,
  candidate_version: 2,
  candidate_percent: 10,
  candidate_since: "2026-10-16T08:00:00Z",
  release_policy: { on_publish: "candidate", stable: { require_candidate: true, min_candidate_runs: 2, step_up_minutes: 10 } },
  versions: [
    { version: 3, spec: SPEC, checksum: "sha256:3", created_by: "lea@acme.dev", channels: [] },
    { version: 2, spec: SPEC, checksum: "sha256:2", channels: ["candidate"] },
    { version: 1, spec: SPEC, checksum: "sha256:1", channels: ["stable"] },
  ],
  ...over,
});

const check = (name: string, passed: boolean | null, detail = "", required = true): AgentReleaseCheck => ({ name, passed, detail, required });
const readiness = (checks: AgentReleaseCheck[], over: Partial<AgentReleaseReadiness> = {}): AgentReleaseReadiness => ({
  version: 2,
  channel: "stable",
  kind: "promote",
  ready: checks.every((c) => c.required === false || c.passed === true),
  checks,
  candidate: { version: 2, runs: 1, succeeded: 1, failed: 0, success_rate: 1 },
  production: { version: 1, runs: 12, succeeded: 11, failed: 1, success_rate: 0.9167 },
  ...over,
});
const history = (rows: Array<Partial<AgentRelease>>): AgentRelease[] =>
  rows.map((r, i) => ({
    id: `r${i}`,
    agent: "reviewer",
    channel: "stable",
    kind: "promote",
    status: "done",
    requested_at: "2026-10-16T09:00:00Z",
    done_at: "2026-10-16T09:00:00Z",
    requested_by: "marc@acme.dev",
    ...r,
  }));

function wrap(node: ReactNode) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{node}</QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  session.email = "lea@acme.dev";
  vi.mocked(api.agentExposure).mockResolvedValue({
    stable_version: 1,
    candidate_version: 2,
    candidate_percent: 10,
    projects: [{ project: "billing-api", source: "project_channel", channel: "candidate", version: 2 }],
  });
  vi.mocked(api.agentMetrics).mockResolvedValue({ agent: "reviewer", days: 30, runs: 0, succeeded: 0, failed: 0, by_version: [] });
  vi.mocked(api.agentReleaseReadiness).mockImplementation(async (_o, _s, version, channel) =>
    channel === "candidate"
      ? readiness([check("eval_suite", true, "2 case(s) pass"), check("newer_than_production", true)], { version, channel })
      : readiness([check("eval_suite", true), check("candidate_runs", false, "1 of 2 finished runs")], { version, channel }),
  );
});

describe("the helpers", () => {
  it("the revision to send is the latest one that sits in no channel and is newer than production", () => {
    expect(unreleasedRevision(agent())?.version).toBe(3);
    expect(unreleasedRevision(agent({ versions: [{ version: 2, spec: SPEC, checksum: "x", channels: ["candidate"] }] }))).toBeUndefined();
    expect(unreleasedRevision(agent({ stable_version: 3, versions: [{ version: 3, spec: SPEC, checksum: "x", channels: ["stable"] }] }))).toBeUndefined();
    expect(unreleasedRevision(agent({ stable_version: 3, versions: [{ version: 3, spec: SPEC, checksum: "x", channels: [] }] }))).toBeUndefined();
  });

  it("a rollback goes back to a revision that was in production, or up to the baseline", () => {
    const a = agent({ stable_version: 4, versions: [4, 3, 2, 1].map((v) => ({ version: v, spec: SPEC, checksum: "x" })) });
    expect(rollbackTargets(a, history([{ kind: "promote", version: 4 }, { kind: "promote", version: 3 }, { kind: "baseline", version: 2 }]))).toEqual([3, 2, 1]);
    expect(rollbackTargets(a, history([{ kind: "promote", version: 4 }, { kind: "promote", version: 3 }]))).toEqual([3]);
    expect(rollbackTargets(a, history([{ kind: "promote", version: 3, status: "pending_approval" }]))).toEqual([]);
  });

  it("a stale sign-in does not block the click: the click asks for a new one", () => {
    const checks = [check("eval_suite", true), check("fresh_authentication", false), check("approvals", null, "", false)];
    expect(blockers(checks)).toEqual([]);
    expect(blockers([...checks, check("candidate_runs", false)]).map((c) => c.name)).toEqual(["candidate_runs"]);
  });

  it("names where an answer's revision comes from, and what an old answer without a source says", () => {
    expect(sourceLabel({ source: "stable", pinned: false })).toBe("production");
    expect(sourceLabel({ source: "project_channel", pinned: false })).toBe("testing");
    expect(sourceLabel({ source: "canary", pinned: false })).toBe("testing, canary");
    expect(sourceLabel({ source: "pin", pinned: true })).toBe("pinned");
    expect(sourceLabel({ source: "pin_channel", channel: "candidate", pinned: true })).toBe("pinned to testing");
    expect(sourceLabel({ source: "explicit", pinned: false })).toBe("explicit");
    expect(sourceLabel({ pinned: true })).toBe("pinned");
    expect(sourceLabel({ pinned: false })).toBe("latest");
    expect(canarySentence({ candidate_version: 5, candidate_percent: 10, source: "stable" })).toBe("10 % of work items run v5");
    expect(canarySentence({ candidate_version: 5, candidate_percent: 10, source: "pin" })).toBeNull();
    expect(canarySentence({ candidate_version: null, candidate_percent: null, source: "stable" })).toBeNull();
  });

  it("the publication sentence follows the channels the answer names", () => {
    const a = { stable_version: 4 };
    expect(publishedSentence({ version: 5, channels: ["stable"] }, a)).toBe("v5 is in production");
    expect(publishedSentence({ version: 5, channels: ["candidate"] }, a)).toBe("v5 saved and sent to testing");
    expect(publishedSentence({ version: 5, channels: [] }, a)).toBe("v5 saved; production still runs v4");
    expect(publishedSentence({ version: 5 }, { stable_version: null })).toBe("v5 saved");
  });

  it("the badges say production, testing with its share, and a waiting approval — in words", () => {
    wrap(<ChannelBadges agent={agent({ pending_release: history([{ status: "pending_approval" }])[0]! })} />);
    expect(screen.getByTestId("channel-production")).toHaveTextContent("Production v1");
    expect(screen.getByTestId("channel-testing")).toHaveTextContent("Testing v2 · 10 %");
    expect(screen.getByTestId("channel-pending")).toHaveTextContent("Approval pending");
    wrap(<RevisionChannels channels={["stable", "candidate"]} />);
    expect(screen.getByTestId("revision-channels")).toHaveTextContent("ProductionTesting");
  });
});

describe("the pipeline", () => {
  it("shows production v1, testing v2 at 10 % and the saved revision v3", async () => {
    wrap(<ReleasePipeline org="acme" agent={agent()} history={history([{ kind: "baseline", version: 1 }])} />);
    expect(await screen.findByTestId("testing-card")).toHaveTextContent("v2 · 10 % of work items");
    expect(screen.getByTestId("production-card")).toHaveTextContent("v1");
    expect(await screen.findByText(/is saved and sits in no channel/)).toHaveTextContent("v3");
    expect(await screen.findByTestId("revision-eval")).toHaveTextContent("Its eval suite passes");
    expect(await screen.findByTestId("who-gets-testing")).toHaveTextContent("billing-api — follows testing");
    expect(screen.getByTestId("who-gets-testing")).toHaveTextContent("10 % of the work items of the projects that follow production");
    expect(await screen.findByTestId("stats-testing")).toHaveTextContent("1 finished run, 100 % succeeded");
    expect(screen.getByTestId("stats-production")).toHaveTextContent("12 finished runs");
  });

  it("sends the saved revision to testing with the chosen share", async () => {
    vi.mocked(api.releaseAgentVersion).mockResolvedValue(history([{ channel: "candidate", version: 3 }])[0]!);
    wrap(<ReleasePipeline org="acme" agent={agent()} history={[]} />);
    const slider = await screen.findByLabelText(/Share of work items: 10 %/);
    fireEvent.change(slider, { target: { value: "30" } });
    fireEvent.click(screen.getByRole("button", { name: "Send to testing" }));
    await waitFor(() =>
      expect(api.releaseAgentVersion).toHaveBeenCalledWith("acme", "reviewer", { channel: "candidate", version: 3, percent: 30 }),
    );
    expect(await screen.findByText("v3 is in testing at 30 %.")).toBeInTheDocument();
  });

  it("holds 'Promote' back while a required check fails, with the check's detail, then lets it through", async () => {
    wrap(<ReleasePipeline org="acme" agent={agent()} history={[]} />);
    fireEvent.click(await screen.findByRole("button", { name: "Promote to production…" }));
    const dialog = await screen.findByRole("dialog");
    const checks = await within(dialog).findByTestId("checks");
    expect(checks.querySelector('[data-check="candidate_runs"]')).toHaveTextContent("1 of 2 finished runs");
    expect(checks.querySelector('[data-check="candidate_runs"]')).toHaveTextContent("fails");
    expect(within(dialog).getByRole("button", { name: "Release to production" })).toBeDisabled();
    expect(within(dialog).getByText(/Not ready/)).toBeInTheDocument();
    expect(within(dialog).getByTestId("needs-sign-in")).toHaveTextContent("Needs a sign-in less than 10 min old.");
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(api.releaseAgentVersion).not.toHaveBeenCalled();

    // Two runs later the server's checklist passes: the button is live, and releasing sends the reason.
    vi.mocked(api.agentReleaseReadiness).mockResolvedValue(
      readiness([check("eval_suite", true), check("candidate_runs", true, "2 of 2 finished runs")]),
    );
    vi.mocked(api.releaseAgentVersion).mockResolvedValue(history([{ version: 2 }])[0]!);
    fireEvent.click(screen.getByRole("button", { name: "Promote to production…" }));
    const second = await screen.findByRole("dialog");
    const go = await within(second).findByRole("button", { name: "Release to production" });
    await waitFor(() => expect(go).toBeEnabled());
    fireEvent.change(within(second).getByLabelText("Reason"), { target: { value: "two runs" } });
    fireEvent.click(go);
    await waitFor(() =>
      expect(api.releaseAgentVersion).toHaveBeenCalledWith("acme", "reviewer", { channel: "stable", version: 2, reason: "two runs" }),
    );
    expect(await within(second).findByText("v2 is in production.")).toBeInTheDocument();
  });

  it("says before the click that a stale sign-in will ask for a new one, and that approvals are needed", async () => {
    vi.mocked(api.agentReleaseReadiness).mockResolvedValue(
      readiness([check("eval_suite", true), check("fresh_authentication", false, "needs a sign-in less than 10 min old"), check("approvals", null, "needs 2 approval(s)", false)]),
    );
    const policy = { on_publish: "candidate" as const, stable: { step_up_minutes: 10, approvals: 2 } };
    wrap(<ReleasePipeline org="acme" agent={agent({ release_policy: policy })} history={[]} />);
    fireEvent.click(await screen.findByRole("button", { name: "Promote to production…" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByTestId("needs-sign-in")).toHaveTextContent("yours is older, so you will be asked to sign in again");
    expect(within(dialog).getByTestId("needs-approvals")).toHaveTextContent("Needs 2 approvals from other people before production moves");
    const go = within(dialog).getByRole("button", { name: "Ask for approval" });
    expect(go).toBeEnabled();
  });

  it("'Roll back to…' lists only the revisions that were in production before", async () => {
    const a = agent({
      stable_version: 4,
      candidate_version: null,
      versions: [4, 3, 2, 1].map((v) => ({ version: v, spec: SPEC, checksum: "x", channels: v === 4 ? (["stable"] as const) : [] })),
    });
    wrap(<ReleasePipeline org="acme" agent={a} history={history([{ kind: "promote", version: 4 }, { kind: "promote", version: 3 }, { kind: "baseline", version: 1 }])} />);
    const select = await screen.findByLabelText("Roll back to");
    const options = within(select).getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["—", "v3", "v1"]);
    fireEvent.change(select, { target: { value: "3" } });
    expect(screen.getByRole("button", { name: "Roll back to v3…" })).toBeEnabled();
  });

  it("holds every button back with the server's sentence when the caller may not release", async () => {
    const sentence = "`reviewer` is an organisation agent: only an org_admin changes it; fork it into your project.";
    wrap(<ReleasePipeline org="acme" agent={agent({ can: { publish: false, manage: false, pin: true, release: false, fork_scopes: [] }, cannot_reason: sentence })} history={history([{ kind: "baseline", version: 1 }])} />);
    expect(await screen.findByRole("button", { name: "Send to testing" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Change share" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Withdraw" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Promote to production…" })).toBeDisabled();
    expect(screen.getByTestId("release-cannot-reason")).toHaveTextContent(sentence);
  });
});

describe("a release waiting for approval", () => {
  const waiting = (over: Partial<AgentRelease> = {}): AgentRelease =>
    history([{ version: 2, status: "pending_approval", approvals_required: 2, requested_by: "lea@acme.dev", reason: "ship it", decisions: [], ...over }])[0]!;

  it("shows who asked and how many approvals it has; the requester cannot approve, and the page says why", async () => {
    wrap(<PendingRelease org="acme" agent={agent({ pending_release: waiting() })} />);
    expect(screen.getByTestId("pending-release")).toHaveTextContent("v2 to production, asked by lea@acme.dev");
    expect(screen.getByTestId("pending-release")).toHaveTextContent("“ship it”");
    expect(screen.getByTestId("approvals-count")).toHaveTextContent("0 of 2 approvals");
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(screen.getByTestId("approve-reason")).toHaveTextContent("Separation of duties: neither the person who asked for this release nor the author of the revision approves it.");
  });

  it("another person approves, and a rejection needs its reason", async () => {
    session.email = "marc@acme.dev";
    vi.mocked(api.decideAgentRelease).mockResolvedValue(waiting());
    wrap(<PendingRelease org="acme" agent={agent({ pending_release: waiting({ decisions: [{ by: "ana@acme.dev", decision: "approve", at: "2026-10-16T09:30:00Z" }] }) })} />);
    expect(screen.getByTestId("approvals-count")).toHaveTextContent("1 of 2 approvals");
    expect(screen.getByText(/ana@acme.dev: approve/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(api.decideAgentRelease).toHaveBeenCalledWith("acme", "reviewer", "r0", { decision: "approve", reason: null }));

    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    const confirm = await screen.findByTestId("confirmation");
    expect(within(confirm).getByRole("button", { name: "Reject" })).toBeDisabled();
    fireEvent.change(within(confirm).getByRole("textbox"), { target: { value: "not before the audit" } });
    await act(async () => {
      fireEvent.click(within(confirm).getByRole("button", { name: "Reject" }));
    });
    expect(api.decideAgentRelease).toHaveBeenLastCalledWith("acme", "reviewer", "r0", { decision: "reject", reason: "not before the audit" });
  });

  it("someone who already approved cannot approve twice, and a caller without the release right is held back", async () => {
    session.email = "ana@acme.dev";
    const { unmount } = wrap(
      <PendingRelease org="acme" agent={agent({ pending_release: waiting({ decisions: [{ by: "ana@acme.dev", decision: "approve", at: "2026-10-16T09:30:00Z" }] }) })} />,
    );
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(screen.getByTestId("approve-reason")).toHaveTextContent("You have already approved this release.");
    unmount();
    session.email = "marc@acme.dev";
    wrap(
      <PendingRelease
        org="acme"
        agent={agent({ pending_release: waiting(), can: { publish: false, manage: false, pin: false, release: false, fork_scopes: [] }, cannot_reason: "only an org_admin" })}
      />,
    );
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(screen.getByTestId("approve-reason")).toHaveTextContent("only an org_admin");
  });
});

describe("the lifecycle tab", () => {
  it("holds the pipeline, the history, the policy and what the page passes in", async () => {
    vi.mocked(api.agentReleases).mockResolvedValue(history([{ kind: "promote", version: 2, previous_version: 1, reason: "first week" }, { kind: "baseline", version: 1, requested_by: "migration" }]));
    wrap(
      <LifecycleTab org="acme" agent={agent()}>
        <p>Agent status here</p>
      </LifecycleTab>,
    );
    expect(await screen.findByTestId("release-pipeline")).toBeInTheDocument();
    const table = await screen.findByTestId("release-history");
    expect(within(table).getByText("v1 → v2")).toBeInTheDocument();
    expect(within(table).getByText("Before channels")).toBeInTheDocument();
    expect(screen.getByText("Agent status here")).toBeInTheDocument();
    expect(screen.getByText("Release policy")).toBeInTheDocument();
  });

  it("saves the release policy it shows, with the gates the author set", async () => {
    vi.mocked(api.agentReleases).mockResolvedValue([]);
    vi.mocked(api.setAgentReleasePolicy).mockResolvedValue(agent());
    wrap(<LifecycleTab org="acme" agent={agent()} />);
    const form = await screen.findByRole("form", { name: "Release policy" });
    fireEvent.change(within(form).getByLabelText("Approvals from other people"), { target: { value: "1" } });
    fireEvent.change(within(form).getByLabelText("Hours in testing"), { target: { value: "24" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save policy" }));
    await waitFor(() =>
      expect(api.setAgentReleasePolicy).toHaveBeenCalledWith("acme", "reviewer", {
        on_publish: "candidate",
        stable: {
          require_candidate: true,
          min_candidate_runs: 2,
          min_success_rate: null,
          min_soak_hours: 24,
          step_up_minutes: 10,
          approvals: 1,
          separation_of_duties: true,
        },
      }),
    );
  });

  it("a policy that saves straight to production sends no testing gate (the API would refuse it)", async () => {
    vi.mocked(api.agentReleases).mockResolvedValue([]);
    vi.mocked(api.setAgentReleasePolicy).mockResolvedValue(agent());
    wrap(<LifecycleTab org="acme" agent={agent()} />);
    const form = await screen.findByRole("form", { name: "Release policy" });
    fireEvent.change(within(form).getByLabelText("When a revision is saved"), { target: { value: "stable" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save policy" }));
    await waitFor(() => expect(api.setAgentReleasePolicy).toHaveBeenCalled());
    expect(vi.mocked(api.setAgentReleasePolicy).mock.calls[0]![2]).toMatchObject({
      on_publish: "stable",
      stable: { require_candidate: false, min_candidate_runs: 0, min_success_rate: null, min_soak_hours: null },
    });
  });
});
