// SPDX-License-Identifier: Apache-2.0
/**
 * Deux parcours du gabarit `dev-complex` pour le mode démo (S22-02) — la carte et la vue processus
 * sont celles que l'API rend (`dev-complex.json`, écrit par `to_graph` et `to_process`), les vies
 * sont écrites à la main :
 *
 * - `#123`, en cours : une spécification refusée une fois, des tests qui échouent puis passent, une
 *   revue qui demande des changements, et la revue de sécurité qui tourne ;
 * - `#120`, fini : la même chaîne jusqu'à la production, avec une CI rouge réparée et le départ du
 *   train approuvé par le capitaine.
 */
import type { JourneyMove, JourneyStep, WorkItemJourney } from "@/lib/types";
import devComplex from "./dev-complex.json";

type Carte = Pick<WorkItemJourney, "workflow_name" | "workflow_version" | "initial" | "graph" | "process">;
const CARTE = devComplex as unknown as Carte;

const ACTEURS: Record<string, [string, string]> = {
  "t-triage": ["triager", "triage"],
  "t-specify": ["spec_writer", "refine"],
  "t-plan": ["planner", "plan"],
  "t-implement": ["developer", "implement"],
  "t-test": ["tester", "verify"],
  "t-review": ["reviewer", "review"],
  "t-security-review": ["security_reviewer", "review"],
  "t-address-review": ["responder", "address_review"],
  "t-release-notes": ["notes_writer", "release_notes"],
  "t-fix-ci": ["ci_fixer", "fix_ci"],
  "t-verify-prod": ["prod_verifier", "verify_prod"],
};

/** Écrit une vie : chaque ligne avance l'horloge, en minutes depuis le début. */
function vie(debut: number) {
  const moves: JourneyMove[] = [];
  const steps: JourneyStep[] = [];
  const iso = (minute: number) => new Date(debut + minute * 60_000).toISOString();
  const rangs = new Map<string, number>();
  const rang = (cle: string) => {
    rangs.set(cle, (rangs.get(cle) ?? 0) + 1);
    return rangs.get(cle)!;
  };
  return {
    moves,
    steps,
    deplacer(
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
      debutMin: number,
      finMin: number | null,
      resultat: {
        status?: string;
        summary?: string;
        verdict?: string;
        cost?: number;
        evidence?: Record<string, unknown>;
      } = {},
    ) {
      const [actor, role] = ACTEURS[transition]!;
      const attempt = rang(transition);
      steps.push({
        id: `r-${transition.slice(2)}-${attempt}`,
        kind: "agent",
        transition_id: transition,
        actor,
        role,
        attempt,
        status: resultat.status ?? (finMin === null ? "running" : "succeeded"),
        started_at: iso(debutMin),
        ended_at: finMin === null ? null : iso(finMin),
        summary: resultat.summary ?? null,
        verdict: resultat.verdict ?? null,
        cost_usd: resultat.cost ?? 0.3,
        model: role === "triage" || role === "release_notes" ? "profile:cheap" : "profile:standard",
        evidence: resultat.evidence ?? {},
      });
    },
    humain(
      transition: string,
      actor: string,
      debutMin: number,
      finMin: number | null,
      decision: { status: string; by?: string; summary: string },
    ) {
      const attempt = rang(transition);
      steps.push({
        id: `hr-${transition.slice(2)}-${attempt}`,
        kind: "human",
        transition_id: transition,
        actor,
        role: "approval",
        attempt,
        status: decision.status,
        started_at: iso(debutMin),
        ended_at: finMin === null ? null : iso(finMin),
        summary: decision.summary,
        decided_by: decision.by ?? null,
        due_at: iso(debutMin + 24 * 60),
        cost_usd: 0,
        evidence: {},
      });
    },
    train(transition: string, env: string, debutMin: number, finMin: number, approbateur?: string) {
      const attempt = rang(transition);
      steps.push({
        id: `rel-${env}-${attempt}`,
        kind: "action",
        transition_id: transition,
        actor: "release_train",
        role: `release to ${env}`,
        attempt,
        status: "verified",
        started_at: iso(debutMin),
        ended_at: iso(finMin),
        summary: `batch ${env === "prod" ? 14 : 31} to ${env}, 3 work item(s)`,
        decided_by: approbateur ?? null,
        cost_usd: 0,
        evidence: {},
      });
    },
  };
}

// Ce que le runner a mesuré ; la couverture, quand elle y est, vient du récit de l'agent (ADR 0045).
const MESURES = ["lint", "tests_failed", "tests_passed", "tests_run", "typecheck"];
const TESTS_ROUGES = {
  tests_run: 412,
  tests_failed: 2,
  tests_passed: false,
  lint: "ok",
  typecheck: "ok",
  measured: MESURES,
};
const TESTS_VERTS = {
  tests_run: 418,
  tests_failed: 0,
  tests_passed: true,
  lint: "ok",
  typecheck: "ok",
  coverage_delta: 1.2,
  measured: MESURES,
};

/** La partie commune : du tri à la revue approuvée, avec un refus, un échec et une demande de changements. */
function jusquALaRevue(v: ReturnType<typeof vie>) {
  v.deplacer(0, null, "inbox", "start", null, "started");
  v.agent("t-triage", 2, 5, {
    summary: "Size M, risk low: the total is computed in one place.",
    cost: 0.04,
  });
  v.deplacer(5, "inbox", "triaged", "nominal", "t-triage");
  v.agent("t-specify", 6, 12, {
    summary: "Specification: deduct credit notes before taxes; 6 allowed paths.",
    cost: 0.41,
  });
  v.deplacer(12, "triaged", "awaiting_spec_approval", "nominal", "t-specify");
  v.humain("t-approve-spec", "product_owners", 13, 40, {
    status: "rejected",
    by: "augustin@varga.dev",
    summary: "Approve the specification of “Credit notes are not deducted from the total”",
  });
  v.deplacer(40, "awaiting_spec_approval", "triaged", "reject", "t-approve-spec", "Also cover partial credit notes.");
  v.agent("t-specify", 41, 46, {
    summary: "Specification v2: partial credit notes covered, rounding rule stated.",
    cost: 0.38,
  });
  v.deplacer(46, "triaged", "awaiting_spec_approval", "nominal", "t-specify");
  v.humain("t-approve-spec", "product_owners", 47, 70, {
    status: "approved",
    by: "augustin@varga.dev",
    summary: "Approve the specification of “Credit notes are not deducted from the total”",
  });
  v.deplacer(70, "awaiting_spec_approval", "spec_approved", "nominal", "t-approve-spec", "human decision: approved");
  v.agent("t-plan", 71, 76, {
    summary: "Plan: 3 steps, 4 files, one migration-free change.",
    cost: 0.52,
  });
  v.deplacer(76, "spec_approved", "planned", "nominal", "t-plan");
  v.agent("t-implement", 77, 100, {
    summary: "Credit notes deducted in compute_total; 7 commits.",
    cost: 2.74,
  });
  v.deplacer(100, "planned", "implemented", "nominal", "t-implement");
  v.agent("t-test", 101, 110, {
    status: "failed",
    summary: "2 tests fail: rounding of partial credit notes.",
    evidence: TESTS_ROUGES,
    cost: 0.62,
  });
  v.deplacer(110, "implemented", "planned", "retry", "t-test", "failing gates: evidence_present");
  v.agent("t-implement", 111, 130, {
    summary: "Rounding fixed with Decimal and a half-even rule.",
    cost: 1.91,
  });
  v.deplacer(130, "planned", "implemented", "nominal", "t-implement");
  v.agent("t-test", 131, 140, {
    summary: "418 tests pass, coverage +1.2.",
    evidence: TESTS_VERTS,
    cost: 0.58,
  });
  v.deplacer(140, "implemented", "tested", "nominal", "t-test");
  v.agent("t-review", 141, 150, {
    summary: "Two remarks: a zero total is not tested; `cn` is a poor name.",
    verdict: "changes_requested",
    cost: 0.47,
  });
  v.deplacer(150, "tested", "addressing_review", "changes_requested", "t-review", "two remarks");
  v.agent("t-address-review", 151, 160, {
    summary: "Both remarks addressed: test added, variable renamed.",
    cost: 0.66,
  });
  v.deplacer(160, "addressing_review", "implemented", "nominal", "t-address-review");
  v.agent("t-test", 161, 168, {
    summary: "420 tests pass.",
    evidence: { ...TESTS_VERTS, tests_run: 420 },
    cost: 0.55,
  });
  v.deplacer(168, "implemented", "tested", "nominal", "t-test");
  v.agent("t-review", 169, 176, {
    summary: "Approved: the change is small, tested and named well.",
    verdict: "approve",
    cost: 0.44,
  });
  v.deplacer(176, "tested", "reviewed", "nominal", "t-review");
}

/** Le ticket `#123`, en cours : la revue de sécurité tourne depuis six minutes. */
export function parcoursEnCours(maintenant: number = Date.now()): WorkItemJourney {
  const debut = maintenant - 183 * 60_000;
  const v = vie(debut);
  jusquALaRevue(v);
  v.agent("t-security-review", 177, null, { cost: 0.21 });
  return {
    ...CARTE,
    work_item_id: "w1",
    tracker_key: "varga/billing-api#123",
    state: "reviewed",
    closed: false,
    moves: v.moves,
    steps: v.steps,
  };
}

/** Le ticket `#120`, en production : la chaîne entière, une CI rouge réparée, le capitaine qui approuve. */
export function parcoursTermine(maintenant: number = Date.now()): WorkItemJourney {
  const debut = maintenant - 26 * 60 * 60_000;
  const v = vie(debut);
  jusquALaRevue(v);
  v.agent("t-security-review", 177, 186, {
    summary: "No finding: inputs validated, no secret, no new dependency.",
    verdict: "approve",
    cost: 0.51,
  });
  v.deplacer(186, "reviewed", "secured", "nominal", "t-security-review");
  v.agent("t-release-notes", 187, 189, {
    summary: "Release notes: credit notes now reduce the invoice total.",
    cost: 0.03,
  });
  v.deplacer(189, "secured", "notes_written", "nominal", "t-release-notes");
  v.deplacer(191, "notes_written", "awaiting_pr_approval", "nominal", "t-open-pr");
  v.humain("t-approve-pr", "maintainers", 191, 230, {
    status: "approved",
    by: "marie@varga.dev",
    summary: "Approve the pull request #456",
  });
  v.deplacer(230, "awaiting_pr_approval", "pr_approved", "nominal", "t-approve-pr", "human decision: approved");
  v.deplacer(262, "pr_approved", "fixing_ci", "retry", "t-merge", "failing gates: ci_green");
  v.agent("t-fix-ci", 263, 275, {
    summary: "The lockfile was stale: regenerated, CI green.",
    cost: 0.37,
  });
  v.deplacer(275, "fixing_ci", "awaiting_pr_approval", "nominal", "t-fix-ci");
  v.humain("t-approve-pr", "maintainers", 275, 300, {
    status: "approved",
    by: "marie@varga.dev",
    summary: "Approve the pull request #456 again: the CI fix changed it",
  });
  v.deplacer(300, "awaiting_pr_approval", "pr_approved", "nominal", "t-approve-pr", "human decision: approved");
  v.deplacer(312, "pr_approved", "integrated", "nominal", "t-merge", "gates green");
  v.train("t-stage", "staging", 320, 335);
  v.deplacer(335, "integrated", "staged", "nominal", "t-stage", "deployment verified");
  v.train("t-release", "prod", 400, 440, "leo@varga.dev");
  v.deplacer(440, "staged", "live", "nominal", "t-release", "deployment verified");
  v.agent("t-verify-prod", 441, 446, {
    summary: "Go: smoke check green, error rate flat.",
    verdict: "approve",
    evidence: { facts: { smoke_ok: true }, measured: ["facts.smoke_ok"] },
    cost: 0.12,
  });
  v.deplacer(446, "live", "verified", "nominal", "t-verify-prod");
  return {
    ...CARTE,
    work_item_id: "w4",
    tracker_key: "varga/billing-api#120",
    state: "verified",
    closed: true,
    moves: v.moves,
    steps: v.steps,
  };
}

/** Le parcours d'un ticket en attente d'une personne (`#124`) : celui de `#123`, arrêté à la spécification. */
export function parcoursEnAttente(maintenant: number = Date.now()): WorkItemJourney {
  const complet = parcoursEnCours(maintenant);
  const coupure = Date.parse(complet.moves.find((m) => m.to === "awaiting_spec_approval")!.at);
  const steps = complet.steps
    .filter((s) => Date.parse(s.started_at ?? "") <= coupure + 60_000)
    .map((s) =>
      Date.parse(s.ended_at ?? "") > coupure + 60_000 ? { ...s, ended_at: null, status: "waiting", decided_by: null } : s,
    );
  return {
    ...complet,
    work_item_id: "w2",
    tracker_key: "varga/billing-api#124",
    state: "awaiting_spec_approval",
    moves: complet.moves.filter((m) => Date.parse(m.at) <= coupure),
    steps,
  };
}

/** Le ticket du mode démo, par identifiant. */
export function parcoursDe(id: string): WorkItemJourney {
  if (id === "w4") return parcoursTermine();
  if (id === "w2") return parcoursEnAttente();
  return parcoursEnCours();
}
