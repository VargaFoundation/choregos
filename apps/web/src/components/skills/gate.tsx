// SPDX-License-Identifier: Apache-2.0
"use client";

import { useState } from "react";
import { egressFlag, useEgressGuard } from "@/components/egress/egress-provider";
import { Badge, Button, ErrorNote, Field, Input, type Tone } from "@/components/ui";
import { api } from "@/lib/api";
import type { Skill, SkillCandidate, SkillFinding, SkillGitPreview, SkillUpdate, SkillVersion } from "@/lib/types";

/**
 * The supply-chain gate of a skill, as the console shows it (S32-06, ADR 0050): a risk tier, what
 * the scan found, and whether the version waits for a second person.
 */

export type Risk = "low" | "medium" | "high";

export function toneOfRisk(risk: string | null | undefined): Tone {
  if (risk === "low") return "ok";
  if (risk === "medium") return "warn";
  if (risk === "high") return "danger";
  return "neutral";
}

/** The badge of a version: its risk, and `pending review` while a high-risk version waits. */
export function RiskBadge({ risk, reviewStatus }: { risk?: string | null; reviewStatus?: string | null }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <Badge tone={toneOfRisk(risk)} data-testid="risque">
        {risk ? `${risk[0]!.toUpperCase()}${risk.slice(1)} risk` : "Not assessed"}
      </Badge>
      {reviewStatus === "pending_review" && (
        <Badge tone="waiting" data-testid="revue">
          Pending review
        </Badge>
      )}
    </span>
  );
}

/** A code shown as words starts with a capital, like everything the console writes (ADR 0048). */
function sentence(text: string): string {
  return text ? `${text[0]!.toUpperCase()}${text.slice(1)}` : text;
}

const SEVERITY_TONE: Record<string, Tone> = { critical: "danger", warning: "warn", info: "neutral" };

/** What the scan found, the most severe first. */
export function sortFindings(findings: SkillFinding[]): SkillFinding[] {
  const rank: Record<string, number> = { critical: 0, warning: 1, info: 2 };
  return [...findings].sort((a, b) => (rank[a.severity] ?? 3) - (rank[b.severity] ?? 3));
}

export function Findings({ findings }: { findings: SkillFinding[] | null | undefined }) {
  if (!findings || findings.length === 0) return <p className="text-xs text-ink-muted">The scan found nothing.</p>;
  return (
    <ul className="space-y-1 text-xs" data-testid="constats">
      {sortFindings(findings).map((finding, index) => (
        <li key={`${finding.code}-${finding.file ?? ""}-${index}`} className="flex flex-wrap items-baseline gap-2">
          <Badge tone={SEVERITY_TONE[finding.severity] ?? "neutral"} dot={false}>
            {sentence(finding.code.replaceAll("_", " "))}
          </Badge>
          <span>{finding.detail}</span>
          {finding.file && (
            <code className="text-ink-muted">
              {finding.file}
              {finding.line ? `:${finding.line}` : ""}
            </code>
          )}
        </li>
      ))}
    </ul>
  );
}

/** A candidate can be picked when it passed the checks and has a name. */
export function importable(candidate: SkillCandidate): boolean {
  return !candidate.problem && !!candidate.name;
}

/**
 * Import from a repository: the preview resolves the reference to a commit and lists the skills
 * found there, each with its risk; the import pins THAT commit, never the branch typed.
 */
export function GitImport({ org, onImported }: { org: string; onImported?: (skills: Skill[]) => void }) {
  const [repo, setRepo] = useState("https://github.com/anthropics/skills");
  const [ref, setRef] = useState("main");
  const [path, setPath] = useState("skills");
  const [preview, setPreview] = useState<SkillGitPreview | null>(null);
  const [chosen, setChosen] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const guard = useEgressGuard();

  async function look() {
    setError(null);
    setDone(null);
    setBusy(true);
    try {
      setPreview(
        await guard((allow) => api.previewSkillsFromGit(org, { repo_url: repo, ref, path, ...egressFlag(allow) }), {
          gesture: `Import skills from ${repo}`,
          origin: `skill-import:${repo.replace(/^https:\/\//, "").replace(/\.git$/, "")}`,
          reason: `importing skills from ${repo}`,
        }),
      );
      setChosen([]);
    } catch (cause) {
      setPreview(null);
      setError(cause instanceof Error ? cause.message : "preview refused");
    } finally {
      setBusy(false);
    }
  }

  async function bring() {
    if (!preview) return;
    setError(null);
    setBusy(true);
    try {
      const skills = await guard(
        (allow) =>
          api.importSkillsFromGit(org, {
            repo_url: preview.repo,
            sha: preview.sha,
            path: preview.path,
            skills: chosen,
            ...egressFlag(allow),
          }),
        {
          gesture: `Import ${chosen.join(", ")} from ${preview.repo}`,
          origin: `skill-import:${preview.repo.replace(/^https:\/\//, "")}`,
          reason: `importing skills from ${preview.repo}`,
        },
      );
      const pending = skills.filter((s) => s.review_status === "pending_review").map((s) => s.slug);
      setDone(
        `${skills.map((s) => `${s.slug} v${s.latest_version}`).join(", ")} imported at ${preview.sha.slice(0, 12)}` +
          (pending.length ? ` — ${pending.join(", ")} waits for a second reviewer` : ""),
      );
      setChosen([]);
      onImported?.(skills);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "import refused");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3 text-sm">
      <p className="text-ink-muted">
        A public repository on github.com or gitlab.com. The preview resolves the branch to its commit; the import pins
        that commit, so the skill cannot change under its agents.
      </p>
      <div className="grid gap-3 md:grid-cols-[2fr_1fr_1fr]">
        <Field label="Repository">
          {(props) => <Input {...props} value={repo} onChange={(e) => setRepo(e.target.value)} />}
        </Field>
        <Field label="Branch, tag or commit">
          {(props) => <Input {...props} value={ref} onChange={(e) => setRef(e.target.value)} />}
        </Field>
        <Field label="Folder">
          {(props) => <Input {...props} value={path} onChange={(e) => setPath(e.target.value)} />}
        </Field>
      </div>
      <Button onClick={() => void look()} disabled={busy || !repo || !ref}>
        Preview
      </Button>
      {preview && (
        <div className="space-y-2" data-testid="apercu">
          <p className="text-xs text-ink-muted">
            <code>{preview.repo}</code> at <code>{preview.sha}</code>
            {preview.allowed ? " · in the allowlist" : " · not in the allowlist: an administrator imports it"}
          </p>
          {preview.skills.length === 0 ? (
            <p className="text-ink-muted">No folder with a SKILL.md here.</p>
          ) : (
            <ul className="space-y-2">
              {preview.skills.map((candidate) => (
                <li key={candidate.path} className="space-y-1 border-t border-line pt-2">
                  <label className="flex flex-wrap items-center gap-2">
                    <input
                      type="checkbox"
                      disabled={!importable(candidate)}
                      checked={!!candidate.name && chosen.includes(candidate.name)}
                      onChange={(e) => {
                        const name = candidate.name ?? "";
                        setChosen((now) => (e.target.checked ? [...now, name] : now.filter((n) => n !== name)));
                      }}
                    />
                    <span className="font-medium">{candidate.name ?? candidate.path}</span>
                    <RiskBadge risk={candidate.risk} />
                    <span className="text-xs text-ink-muted">
                      {candidate.files} files · {candidate.licence ?? "no licence"}
                      {candidate.existing_version ? ` · next version of v${candidate.existing_version}` : ""}
                    </span>
                  </label>
                  {candidate.problem ? (
                    <p className="text-xs text-danger">{candidate.problem}</p>
                  ) : (
                    <Findings findings={candidate.findings} />
                  )}
                </li>
              ))}
            </ul>
          )}
          <Button tone="accent" onClick={() => void bring()} disabled={busy || chosen.length === 0}>
            Import {chosen.length || ""} at {preview.sha.slice(0, 12)}
          </Button>
        </div>
      )}
      {done && <p role="status">{done}</p>}
      {error && <ErrorNote>{error}</ErrorNote>}
    </div>
  );
}

/** Who may approve: an administrator — the API decides — who is not the importer. */
export function canApprove(version: Pick<SkillVersion, "review_status" | "created_by">, me: string | null | undefined): boolean {
  return version.review_status === "pending_review" && !!me && me.toLowerCase() !== (version.created_by ?? "").toLowerCase();
}

/** A version's gate: its risk, where it comes from, what the scan found, and the second reviewer. */
export function VersionGate({
  org,
  slug,
  version,
  me,
  onChanged,
}: {
  org: string;
  slug: string;
  version: SkillVersion;
  me?: string | null;
  onChanged?: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function approve() {
    setError(null);
    setBusy(true);
    try {
      await api.approveSkillVersion(org, slug, version.version);
      onChanged?.();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "approval refused");
    } finally {
      setBusy(false);
    }
  }
  const mine = version.review_status === "pending_review" && !canApprove(version, me);
  return (
    <div className="space-y-3 text-sm" data-testid="porte">
      <div className="flex flex-wrap items-center gap-2">
        <RiskBadge risk={version.risk} reviewStatus={version.review_status} />
        {version.reviewed_by && (
          <span className="text-xs text-ink-muted">Approved by {version.reviewed_by}</span>
        )}
      </div>
      {version.source && (
        <p className="text-xs text-ink-muted">
          From <code>{version.source.repo}</code> at <code>{version.source.sha.slice(0, 12)}</code>
          {version.source.path ? (
            <>
              {" "}
              in <code>{version.source.path}</code>
            </>
          ) : null}{" "}
          · {version.source.licence ?? "no licence found"}
        </p>
      )}
      <Findings findings={version.findings} />
      {version.review_status === "pending_review" && (
        <div className="space-y-1">
          <p className="text-xs text-ink-muted">
            No agent may carry this version until an administrator other than {version.created_by ?? "its importer"} approves
            it.
          </p>
          <Button tone="primary" onClick={() => void approve()} disabled={busy || mine}>
            Approve v{version.version}
          </Button>
          {mine && <p className="text-xs text-ink-muted">You imported it: a second person approves it.</p>}
        </div>
      )}
      {error && <ErrorNote>{error}</ErrorNote>}
    </div>
  );
}

/** Whether the repository moved: a proposal — importing its SHA is a separate, deliberate step. */
export function UpdateCheck({ org, slug }: { org: string; slug: string }) {
  const [update, setUpdate] = useState<SkillUpdate | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function check() {
    setError(null);
    try {
      setUpdate(await api.checkSkillUpdate(org, slug));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "check refused");
    }
  }
  return (
    <div className="space-y-2 text-sm">
      <Button onClick={() => void check()}>Check for an update</Button>
      {update &&
        (update.up_to_date ? (
          <p role="status">Up to date at {update.sha.slice(0, 12)}.</p>
        ) : (
          <div role="status" className="space-y-1 text-xs">
            <p>
              {update.ref} is now at <code>{update.sha}</code>
              {update.problem ? ` — ${update.problem}` : ""}.
            </p>
            {!update.problem && (
              <p className="text-ink-muted">
                Added {(update.added ?? []).join(", ") || "—"} · modified {(update.modified ?? []).join(", ") || "—"} ·
                removed {(update.removed ?? []).join(", ") || "—"}. Nothing is applied: import this commit to publish it.
              </p>
            )}
          </div>
        ))}
      {error && <ErrorNote>{error}</ErrorNote>}
    </div>
  );
}
