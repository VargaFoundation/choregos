// SPDX-License-Identifier: Apache-2.0
"use client";

/**
 * "Try it" (S32-03): run the draft once on a sample input, in one project, under a budget capped at
 * 0.50 $, without publishing it. The run is the usual machinery (the orchestrator's `AgentTrial`) on a
 * trial work item the board never lists; this bench starts it, then reads the run until it ends.
 * Also: the draft's eval suite — the cases a publication must pass.
 */
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Badge, Button, ErrorNote, Input, Select, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { usd } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { AgentEvalCase, AgentEvalResult, AgentSpec, AgentTrial } from "@/lib/types";

const TRIAL_CAP_USD = 0.5;
const ENDED = new Set(["succeeded", "failed", "cancelled", "timed_out"]);

function lines(text: string): string[] {
  return text
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);
}

/** The eval suite of the draft: cases that its instructions, rendered on a sample, must satisfy. */
export function EvalSuiteEditor({ cases, onChange }: { cases: AgentEvalCase[]; onChange: (cases: AgentEvalCase[]) => void }) {
  const update = (index: number, patch: Partial<AgentEvalCase>) =>
    onChange(cases.map((c, i) => (i === index ? { ...c, ...patch } : c)));
  return (
    <div className="space-y-3" data-testid="suite-d-evals">
      <p className="text-xs text-ink-muted">
        Each case renders the instructions on its sample and checks what the text must and must not contain — no model, the
        same answer every time. Publishing needs every case of the published version and of the new one to pass.
      </p>
      {cases.map((c, index) => (
        <div key={index} className="grid gap-2 rounded border border-line p-3 sm:grid-cols-2">
          <label className="space-y-1">
            <span className="text-xs text-ink-muted">Case name</span>
            <Input value={c.name} onChange={(e) => update(index, { name: e.target.value })} />
          </label>
          <label className="space-y-1">
            <span className="text-xs text-ink-muted">Case sample title</span>
            <Input value={c.input?.title ?? ""} onChange={(e) => update(index, { input: { ...c.input, title: e.target.value } })} />
          </label>
          <label className="space-y-1">
            <span className="text-xs text-ink-muted">Must contain (one per line)</span>
            <Textarea
              rows={2}
              defaultValue={(c.must_contain ?? []).join("\n")}
              onChange={(e) => update(index, { must_contain: lines(e.target.value) })}
            />
          </label>
          <label className="space-y-1">
            <span className="text-xs text-ink-muted">Must not contain (one per line)</span>
            <Textarea
              rows={2}
              defaultValue={(c.must_not_contain ?? []).join("\n")}
              onChange={(e) => update(index, { must_not_contain: lines(e.target.value) })}
            />
          </label>
          <div className="sm:col-span-2">
            <Button size="sm" onClick={() => onChange(cases.filter((_, i) => i !== index))}>
              Remove the case<span className="sr-only"> {c.name}</span>
            </Button>
          </div>
        </div>
      ))}
      <Button size="sm" onClick={() => onChange([...cases, { name: `case ${cases.length + 1}`, input: { title: "" }, must_contain: [] }])}>
        Add a case
      </Button>
    </div>
  );
}

export function EvalResults({ results }: { results: AgentEvalResult[] }) {
  if (results.length === 0) return null;
  return (
    <ul className="space-y-1 text-sm" aria-label="Eval results">
      {results.map((r) => (
        <li key={r.name} className="flex flex-wrap items-center gap-2">
          <Badge tone={r.passed ? "succeeded" : "failed"}>{r.passed ? "Passed" : "Failed"}</Badge>
          <span className="font-mono">{r.name}</span>
          {!r.passed && (
            <span className="text-xs text-ink-muted">
              {r.error ?? [...(r.missing ?? []).map((m) => `missing «${m}»`), ...(r.forbidden ?? []).map((f) => `contains «${f}»`)].join("; ")}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

/** The bench: a project, a sample, a budget — and the run, read until it ends. */
export function TrialBench({ spec, agent }: { spec: AgentSpec; agent?: string }) {
  const { org } = useSession();
  const projects = useQuery({ queryKey: ["projects", org], queryFn: () => api.projects(org) });
  const [project, setProject] = useState("");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [budget, setBudget] = useState("0.25");
  const [trial, setTrial] = useState<AgentTrial | null>(null);
  const [error, setError] = useState<string | null>(null);
  const run = useQuery({
    queryKey: ["run", trial?.run_id],
    queryFn: () => api.run(trial!.run_id),
    enabled: Boolean(trial),
    retry: false,
    refetchInterval: (query) => (query.state.data && ENDED.has(query.state.data.status) ? false : 2000),
  });
  const chosen = project || projects.data?.items[0]?.slug || "";

  async function start() {
    setError(null);
    setTrial(null);
    try {
      setTrial(
        await api.startAgentTrial(org, {
          project: chosen,
          spec,
          input: { title, body },
          budget_usd: Math.min(Number(budget) || TRIAL_CAP_USD, TRIAL_CAP_USD),
          agent: agent ?? null,
        }),
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Refused");
    }
  }

  return (
    <section className="space-y-3 rounded-md border border-line p-4" aria-label="Try it" data-testid="banc-d-essai">
      <h3 className="text-sm font-semibold">Try it</h3>
      <p className="text-xs text-ink-muted">
        Runs the draft once, without publishing it, in a project of yours — its model profiles, backend and tools apply —
        under a budget of {usd(TRIAL_CAP_USD)} at most.
      </p>
      <div className="grid gap-2 sm:grid-cols-2">
        <label className="space-y-1">
          <span className="text-xs text-ink-muted">Project</span>
          <Select value={chosen} onChange={(e) => setProject(e.target.value)}>
            {(projects.data?.items ?? []).map((p) => (
              <option key={p.slug} value={p.slug}>
                {p.name ?? p.slug}
              </option>
            ))}
          </Select>
        </label>
        <label className="space-y-1">
          <span className="text-xs text-ink-muted">Trial budget (USD, 0.50 at most)</span>
          <Input type="number" min={0.01} max={TRIAL_CAP_USD} step="0.05" value={budget} onChange={(e) => setBudget(e.target.value)} />
        </label>
        <label className="space-y-1 sm:col-span-2">
          <span className="text-xs text-ink-muted">Sample title</span>
          <Input value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <label className="space-y-1 sm:col-span-2">
          <span className="text-xs text-ink-muted">Sample body</span>
          <Textarea rows={3} value={body} onChange={(e) => setBody(e.target.value)} />
        </label>
      </div>
      <Button onClick={() => void start()} disabled={!chosen || !title}>
        Try it
      </Button>
      {error && <ErrorNote>{error}</ErrorNote>}
      {trial && (
        <div className="space-y-2" data-testid="resultat-d-essai" role="status">
          <p className="text-sm">
            Trial run <span className="font-mono">{trial.run_id}</span> in <span className="font-mono">{trial.project}</span>,
            capped at {usd(trial.budget_usd)}: {run.data?.status ?? trial.status}
            {run.data?.cost_usd != null && ` · spent ${usd(run.data.cost_usd)}`}
          </p>
          {run.data?.result?.summary && <p className="text-sm">{run.data.result.summary}</p>}
          <EvalResults results={trial.evals ?? []} />
        </div>
      )}
    </section>
  );
}
