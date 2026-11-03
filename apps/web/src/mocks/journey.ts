// SPDX-License-Identifier: Apache-2.0
/**
 * Two journeys of the `dev-complex` template for the demo mode (S22-02): the map and the process
 * view are the ones the API returns (`dev-complex.json`, written by `to_graph` and `to_process`),
 * the lives are written by hand:
 *
 * - `#123`, in progress: a specification refused once, tests that fail then pass, a review that
 *   asks for changes, and the security review running;
 * - `#120`, finished: the same chain up to production, with a red CI repaired and the train's
 *   departure approved by the captain.
 */
import type { JourneyMove, JourneyStep, WorkItemJourney } from "@/lib/types";
import devComplex from "./dev-complex.json";

type MapSource = Pick<WorkItemJourney, "workflow_name" | "workflow_version" | "initial" | "graph" | "process">;
const MAP_SOURCE = devComplex as unknown as MapSource;

const ACTORS: Record<string, [string, string]> = {
  "t-triage": ["triager", "triage"],
  "t-specify": ["spec_writer", "refine"],
  "t-plan": ["planner", "plan"],
  "t-implement": ["developer", "implement"],
  "t-test": ["tester", "verify"],
  "t-document": ["docs_writer", "document"],
  "t-review": ["reviewer", "review"],
  "t-security-review": ["security_reviewer", "review"],
  "t-address-review": ["responder", "address_review"],
  "t-release-notes": ["notes_writer", "release_notes"],
  "t-fix-ci": ["ci_fixer", "fix_ci"],
  "t-verify-prod": ["prod_verifier", "verify_prod"],
};

/** Writes a life: every line moves the clock on, in minutes since the start. */
function life(start: number) {
  const moves: JourneyMove[] = [];
  const steps: JourneyStep[] = [];
  const iso = (minute: number) => new Date(start + minute * 60_000).toISOString();
  const ranks = new Map<string, number>();
  const rank = (key: string) => {
    ranks.set(key, (ranks.get(key) ?? 0) + 1);
    return ranks.get(key)!;
  };
  return {
    moves,
    steps,
    move(
      minute: number,
      from: string | null,
      to: string,
      kind: JourneyMove["kind"],
      transition_id: string | null = null,
      reason?: string,
    ) {
      moves.push({
        at: iso(minute),
        from,
        to,
        kind,
        transition_id,
        reason: reason ?? null,
      });
    },
    agent(
      transition: string,
      startMin: number,
      endMin: number | null,
      outcome: {
        status?: string;
        summary?: string;
        verdict?: string;
        cost?: number;
        evidence?: Record<string, unknown>;
      } = {},
    ) {
      const [actor, role] = ACTORS[transition]!;
      const attempt = rank(transition);
      steps.push({
        id: `r-${transition.slice(2)}-${attempt}`,
        kind: "agent",
        transition_id: transition,
        actor,
        role,
        attempt,
        status: outcome.status ?? (endMin === null ? "running" : "succeeded"),
        started_at: iso(startMin),
        ended_at: endMin === null ? null : iso(endMin),
        summary: outcome.summary ?? null,
        verdict: outcome.verdict ?? null,
        cost_usd: outcome.cost ?? 0.3,
        model: role === "triage" || role === "release_notes" ? "profile:cheap" : "profile:standard",
        evidence: outcome.evidence ?? {},
      });
    },
    human(
      transition: string,
      actor: string,
      startMin: number,
      endMin: number | null,
      decision: { status: string; by?: string; summary: string },
    ) {
      const attempt = rank(transition);
      steps.push({
        id: `hr-${transition.slice(2)}-${attempt}`,
        kind: "human",
        transition_id: transition,
        actor,
        role: "approval",
        attempt,
        status: decision.status,
        started_at: iso(startMin),
        ended_at: endMin === null ? null : iso(endMin),
        summary: decision.summary,
        decided_by: decision.by ?? null,
        due_at: iso(startMin + 24 * 60),
        cost_usd: 0,
        evidence: {},
      });
    },
    train(transition: string, env: string, startMin: number, endMin: number, approver?: string) {
      const attempt = rank(transition);
      steps.push({
        id: `rel-${env}-${attempt}`,
        kind: "action",
        transition_id: transition,
        actor: "release_train",
        role: `release to ${env}`,
        attempt,
        status: "verified",
        started_at: iso(startMin),
        ended_at: iso(endMin),
        summary: `batch ${env === "prod" ? 14 : 31} to ${env}, 3 work item(s)`,
        decided_by: approver ?? null,
        cost_usd: 0,
        evidence: {},
      });
    },
  };
}

// What the runner measured; the coverage, when present, comes from the agent's account (ADR 0045).
const MEASURED = ["lint", "tests_failed", "tests_passed", "tests_run", "typecheck"];
const FAILING_TESTS = {
  tests_run: 412,
  tests_failed: 2,
  tests_passed: false,
  lint: "ok",
  typecheck: "ok",
  measured: MEASURED,
};
const PASSING_TESTS = {
  tests_run: 418,
  tests_failed: 0,
  tests_passed: true,
  lint: "ok",
  typecheck: "ok",
  coverage_delta: 1.2,
  measured: MEASURED,
};

/** The common part: from triage to the approved review, with a refusal, a failure and a request for changes. */
function untilReview(v: ReturnType<typeof life>) {
  v.move(0, null, "inbox", "start", null, "started");
  v.agent("t-triage", 2, 5, {
    summary: "Size M, risk low: the total is computed in one place.",
    cost: 0.04,
  });
  v.move(5, "inbox", "triaged", "nominal", "t-triage");
  v.agent("t-specify", 6, 12, {
    summary: "Specification: deduct credit notes before taxes; 6 allowed paths.",
    cost: 0.41,
  });
  v.move(12, "triaged", "awaiting_spec_approval", "nominal", "t-specify");
  v.human("t-approve-spec", "product_owners", 13, 40, {
    status: "rejected",
    by: "augustin@acme.dev",
    summary: "Approve the specification of “Credit notes are not deducted from the total”",
  });
  v.move(40, "awaiting_spec_approval", "triaged", "reject", "t-approve-spec", "Also cover partial credit notes.");
  v.agent("t-specify", 41, 46, {
    summary: "Specification v2: partial credit notes covered, rounding rule stated.",
    cost: 0.38,
  });
  v.move(46, "triaged", "awaiting_spec_approval", "nominal", "t-specify");
  v.human("t-approve-spec", "product_owners", 47, 70, {
    status: "approved",
    by: "augustin@acme.dev",
    summary: "Approve the specification of “Credit notes are not deducted from the total”",
  });
  v.move(70, "awaiting_spec_approval", "spec_approved", "nominal", "t-approve-spec", "human decision: approved");
  v.agent("t-plan", 71, 76, {
    summary: "Plan: 3 steps, 4 files, one migration-free change.",
    cost: 0.52,
  });
  v.move(76, "spec_approved", "planned", "nominal", "t-plan");
  v.agent("t-implement", 77, 100, {
    summary: "Credit notes deducted in compute_total; 7 commits.",
    cost: 2.74,
  });
  v.move(100, "planned", "implemented", "nominal", "t-implement");
  v.agent("t-test", 101, 110, {
    status: "failed",
    summary: "2 tests fail: rounding of partial credit notes.",
    evidence: FAILING_TESTS,
    cost: 0.62,
  });
  v.move(110, "implemented", "planned", "retry", "t-test", "failing gates: evidence_present");
  v.agent("t-implement", 111, 130, {
    summary: "Rounding fixed with Decimal and a half-even rule.",
    cost: 1.91,
  });
  v.move(130, "planned", "implemented", "nominal", "t-implement");
  v.agent("t-test", 131, 140, {
    summary: "418 tests pass, coverage +1.2.",
    evidence: PASSING_TESTS,
    cost: 0.58,
  });
  v.move(140, "implemented", "tested", "nominal", "t-test");
  v.agent("t-document", 141, 143, {
    summary: "Documented in docs/billing/credit-notes.md and CHANGELOG.md.",
    cost: 0.21,
  });
  v.move(143, "tested", "documented", "nominal", "t-document");
  v.agent("t-review", 144, 150, {
    summary: "Two remarks: a zero total is not tested; `cn` is a poor name.",
    verdict: "changes_requested",
    cost: 0.47,
  });
  v.move(150, "documented", "addressing_review", "changes_requested", "t-review", "two remarks");
  v.agent("t-address-review", 151, 160, {
    summary: "Both remarks addressed: test added, variable renamed.",
    cost: 0.66,
  });
  v.move(160, "addressing_review", "implemented", "nominal", "t-address-review");
  v.agent("t-test", 161, 168, {
    summary: "420 tests pass.",
    evidence: { ...PASSING_TESTS, tests_run: 420 },
    cost: 0.55,
  });
  v.move(168, "implemented", "tested", "nominal", "t-test");
  v.agent("t-document", 169, 171, {
    summary: "Documentation follows the corrected code: the zero-total case is described.",
    cost: 0.18,
  });
  v.move(171, "tested", "documented", "nominal", "t-document");
  v.agent("t-review", 172, 176, {
    summary: "Approved: the change is small, tested and named well.",
    verdict: "approve",
    cost: 0.44,
  });
  v.move(176, "documented", "reviewed", "nominal", "t-review");
}

/** Work item `#123`, in progress: the security review has been running for six minutes. */
export function journeyInProgress(now: number = Date.now()): WorkItemJourney {
  const start = now - 183 * 60_000;
  const v = life(start);
  untilReview(v);
  v.agent("t-security-review", 177, null, { cost: 0.21 });
  return {
    ...MAP_SOURCE,
    work_item_id: "w1",
    tracker_key: "acme/billing-api#123",
    state: "reviewed",
    closed: false,
    moves: v.moves,
    steps: v.steps,
  };
}

/** Work item `#120`, in production: the whole chain, a red CI repaired, the captain approving. */
export function journeyFinished(now: number = Date.now()): WorkItemJourney {
  const start = now - 26 * 60 * 60_000;
  const v = life(start);
  untilReview(v);
  v.agent("t-security-review", 177, 186, {
    summary: "No finding: inputs validated, no secret, no new dependency.",
    verdict: "approve",
    cost: 0.51,
  });
  v.move(186, "reviewed", "secured", "nominal", "t-security-review");
  v.agent("t-release-notes", 187, 189, {
    summary: "Release notes: credit notes now reduce the invoice total.",
    cost: 0.03,
  });
  v.move(189, "secured", "notes_written", "nominal", "t-release-notes");
  v.move(191, "notes_written", "awaiting_pr_approval", "nominal", "t-open-pr");
  v.human("t-approve-pr", "maintainers", 191, 230, {
    status: "approved",
    by: "marie@acme.dev",
    summary: "Approve the pull request #456",
  });
  v.move(230, "awaiting_pr_approval", "pr_approved", "nominal", "t-approve-pr", "human decision: approved");
  v.move(262, "pr_approved", "fixing_ci", "retry", "t-merge", "failing gates: ci_green");
  v.agent("t-fix-ci", 263, 275, {
    summary: "The lockfile was stale: regenerated, CI green.",
    cost: 0.37,
  });
  v.move(275, "fixing_ci", "awaiting_pr_approval", "nominal", "t-fix-ci");
  v.human("t-approve-pr", "maintainers", 275, 300, {
    status: "approved",
    by: "marie@acme.dev",
    summary: "Approve the pull request #456 again: the CI fix changed it",
  });
  v.move(300, "awaiting_pr_approval", "pr_approved", "nominal", "t-approve-pr", "human decision: approved");
  v.move(312, "pr_approved", "integrated", "nominal", "t-merge", "gates green");
  v.train("t-stage", "staging", 320, 335);
  v.move(335, "integrated", "staged", "nominal", "t-stage", "deployment verified");
  v.train("t-release", "prod", 400, 440, "leo@acme.dev");
  v.move(440, "staged", "live", "nominal", "t-release", "deployment verified");
  v.agent("t-verify-prod", 441, 446, {
    summary: "Go: smoke check green, error rate flat.",
    verdict: "approve",
    evidence: { facts: { smoke_ok: true }, measured: ["facts.smoke_ok"] },
    cost: 0.12,
  });
  v.move(446, "live", "verified", "nominal", "t-verify-prod");
  return {
    ...MAP_SOURCE,
    work_item_id: "w4",
    tracker_key: "acme/billing-api#120",
    state: "verified",
    closed: true,
    moves: v.moves,
    steps: v.steps,
  };
}

/** The journey of a work item waiting for a person (`#124`): `#123`'s, stopped at the specification. */
export function journeyWaiting(now: number = Date.now()): WorkItemJourney {
  const full = journeyInProgress(now);
  const cut = Date.parse(full.moves.find((m) => m.to === "awaiting_spec_approval")!.at);
  const steps = full.steps
    .filter((s) => Date.parse(s.started_at ?? "") <= cut + 60_000)
    .map((s) =>
      Date.parse(s.ended_at ?? "") > cut + 60_000 ? { ...s, ended_at: null, status: "waiting", decided_by: null } : s,
    );
  return {
    ...full,
    work_item_id: "w2",
    tracker_key: "acme/billing-api#124",
    state: "awaiting_spec_approval",
    moves: full.moves.filter((m) => Date.parse(m.at) <= cut),
    steps,
  };
}

/** The demo-mode work item, by identifier. */
export function journeyOf(id: string): WorkItemJourney {
  if (id === "w4") return journeyFinished();
  if (id === "w2") return journeyWaiting();
  return journeyInProgress();
}
