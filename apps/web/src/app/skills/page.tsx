// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useRef, useState } from "react";
import { Glossary } from "@/components/glossary";
import { GitImport, RiskBadge } from "@/components/skills/gate";
import { SkillView } from "@/components/skills/skill-view";
import { SkillTabs } from "@/components/skills/tabs";
import { Card, Empty, ErrorNote } from "@/components/ui";
import { OrgPageHeader } from "@/components/org-page-header";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";
import { RESERVED_SKILL_SLUGS, skillHref } from "@/lib/skill-slug";

/**
 * The organisation's skill library (ADR 0033): a folder whose `SKILL.md` says when
 * to use it, imported as a zip, versioned, never rewritten. A skill declares no permission:
 * what the agent can do is said by its version, and the runner checks each skill's fingerprint.
 */
export default function SkillsPage() {
  return (
    <Suspense fallback={<Empty>Reading the skills…</Empty>}>
      <SkillsOrOne />
    </Suspense>
  );
}

/**
 * `?skill=<slug>` shows that skill in place of the list (S55-18): the way to open a skill published
 * under a slug a console page took before S55-17, whose own page is that console page.
 */
function SkillsOrOne() {
  const shown = useSearchParams().get("skill");
  return shown ? <SkillView slug={shown} /> : <Skills />;
}

function Skills() {
  const { org } = useSession();
  const client = useQueryClient();
  const skills = useQuery({ queryKey: ["skills", org], queryFn: () => api.skills(org) });
  const fileInput = useRef<HTMLInputElement>(null);
  const [erreur, setErreur] = useState<string | null>(null);
  const [fait, setFait] = useState<string | null>(null);
  async function importer(archive: File) {
    setErreur(null);
    try {
      const skill = await api.importSkill(org, archive);
      setFait(
        `${skill.slug} v${skill.latest_version} published` +
          (skill.review_status === "pending_review" ? " — high risk: it waits for a second reviewer" : ""),
      );
      await client.invalidateQueries({ queryKey: ["skills", org] });
    } catch (cause) {
      setErreur(cause instanceof Error ? cause.message : "import refused");
    } finally {
      if (fileInput.current) fileInput.current.value = "";
    }
  }
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <OrgPageHeader title="Skills"
          eyebrow="What agents know how to do"
          description={
            <>
              A skill is a folder: a <code>SKILL.md</code> that says when to use it, and the files it brings. An
              agent&apos;s version names the skills it carries; the runner lays them where its backend reads them, and
              checks each one against its digest. A skill grants nothing — <code>allowed-tools</code> is refused.
            </>
          }
        />
        <Glossary ici="skills" />
        <SkillTabs active="installed" />
      </div>
      <Card title="Installed">
        {skills.error ? (
          <ErrorNote>{String(skills.error)}</ErrorNote>
        ) : !skills.data ? (
          <p className="text-sm text-ink-muted">Reading the installed skills…</p>
        ) : skills.data.length === 0 ? (
          <Empty
            title="No skill yet"
            action={
              <Link href="/skills/library" className="text-sm underline">
                Browse the library
              </Link>
            }
          >
            Pick default skills from the library, or import a zip or a repository below.
          </Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="installed-skills">
              <thead className="text-left text-xs text-ink-muted">
                <tr>
                  <th className="py-1 font-normal">Skill</th>
                  <th className="py-1 font-normal">Version</th>
                  <th className="py-1 font-normal">Gate</th>
                  <th className="py-1 font-normal">Used by</th>
                </tr>
              </thead>
              <tbody>
                {skills.data.map((skill) => (
                  <tr key={skill.slug} className="border-t border-line">
                    <td className="py-2">
                      <Link href={skillHref(skill.slug)} className="font-medium hover:underline">
                        {skill.slug}
                      </Link>
                      {skill.description && <p className="text-xs text-ink-muted">{skill.description}</p>}
                      {RESERVED_SKILL_SLUGS.includes(skill.slug) && (
                        <p className="text-xs text-ink-muted" data-testid={`skill-hidden-${skill.slug}`}>
                          This name is now used by a console page: the skill opens here, on the list. To give it a
                          page of its own, publish it under another name in its SKILL.md.
                        </p>
                      )}
                    </td>
                    <td>v{skill.latest_version}</td>
                    <td>
                      <RiskBadge risk={skill.risk} reviewStatus={skill.review_status} />
                    </td>
                    <td className="text-xs">
                      {(skill.used_by ?? []).length === 0
                        ? "—"
                        : (skill.used_by ?? []).map((agent) => (
                            <Link key={agent} href={`/agents/${agent.split("@")[0]}`} className="mr-2 underline">
                              {agent}
                            </Link>
                          ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <Card title="Import from a repository">
        <GitImport org={org} onImported={() => void client.invalidateQueries({ queryKey: ["skills", org] })} />
      </Card>
      <Card title="Import a skill">
        <div className="space-y-2 text-sm">
          <p className="text-ink-muted">
            A zip of the folder (at most 256 files, 5 MiB). The same name publishes its next version. Each version is
            assessed: a script, an executable file, an outbound URL, a credential or an MCP reference makes it high
            risk, and it waits for a second administrator before an agent may carry it.
          </p>
          <label className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-ink-muted">Archive</span>
            <input
              className="min-w-0 max-w-full"
              ref={fileInput}
              type="file"
              accept=".zip,application/zip"
              onChange={(event) => {
                const archive = event.target.files?.[0];
                if (archive) void importer(archive);
              }}
            />
          </label>
          {fait && <p role="status">{fait}</p>}
          {erreur && <ErrorNote>{erreur}</ErrorNote>}
        </div>
      </Card>
    </div>
  );
}
