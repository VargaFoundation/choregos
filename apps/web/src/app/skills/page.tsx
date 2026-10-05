// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRef, useState } from "react";
import { Eyebrow, Heading } from "@varga/design-system";
import { Card, Empty, ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";

/**
 * La bibliothèque de skills de l'organisation (ADR 0033) : un dossier dont le `SKILL.md` dit quand
 * s'en servir, importé en zip, versionné, jamais réécrit. Un skill ne déclare aucune permission :
 * ce que l'agent peut faire, sa version le dit, et le runner vérifie l'empreinte de chaque skill.
 */
export default function SkillsPage() {
  const { org } = useSession();
  const client = useQueryClient();
  const skills = useQuery({ queryKey: ["skills", org], queryFn: () => api.skills(org) });
  const fichier = useRef<HTMLInputElement>(null);
  const [erreur, setErreur] = useState<string | null>(null);
  const [fait, setFait] = useState<string | null>(null);
  async function importer(archive: File) {
    setErreur(null);
    try {
      const skill = await api.importSkill(org, archive);
      setFait(`${skill.slug} v${skill.latest_version} published`);
      await client.invalidateQueries({ queryKey: ["skills", org] });
    } catch (cause) {
      setErreur(cause instanceof Error ? cause.message : "import refused");
    } finally {
      if (fichier.current) fichier.current.value = "";
    }
  }
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <Eyebrow>what agents know how to do</Eyebrow>
        <Heading as="h1" size="xl">
          skills
        </Heading>
        <p className="max-w-3xl text-sm text-ink-muted">
          A skill is a folder: a <code>SKILL.md</code> that says when to use it, and the files it brings. An agent&apos;s
          version names the skills it carries; the runner lays them where its backend reads them, and checks each one
          against its digest. A skill grants nothing — <code>allowed-tools</code> is refused.{" "}
          <Link href="/agents" className="underline">
            the agents
          </Link>
        </p>
      </div>
      <Card title="library">
        {skills.error ? (
          <ErrorNote>{String(skills.error)}</ErrorNote>
        ) : !skills.data ? (
          <p className="text-sm text-ink-muted">reading the library…</p>
        ) : skills.data.length === 0 ? (
          <Empty title="no skill yet">import a zip below.</Empty>
        ) : (
          <table className="w-full text-sm" data-testid="bibliotheque">
            <thead className="text-left text-xs text-ink-muted">
              <tr>
                <th className="py-1 font-normal">skill</th>
                <th className="py-1 font-normal">version</th>
                <th className="py-1 font-normal">used by</th>
              </tr>
            </thead>
            <tbody>
              {skills.data.map((skill) => (
                <tr key={skill.slug} className="border-t border-line">
                  <td className="py-2">
                    <Link href={`/skills/${skill.slug}`} className="font-medium hover:underline">
                      {skill.slug}
                    </Link>
                    {skill.description && <p className="text-xs text-ink-muted">{skill.description}</p>}
                  </td>
                  <td>v{skill.latest_version}</td>
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
        )}
      </Card>
      <Card title="import a skill">
        <div className="space-y-2 text-sm">
          <p className="text-ink-muted">
            A zip of the folder (at most 64 files, 512 KiB). The same name publishes its next version.
          </p>
          <label className="flex items-center gap-2">
            <span className="text-xs text-ink-muted">archive</span>
            <input
              ref={fichier}
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
