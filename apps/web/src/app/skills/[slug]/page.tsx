// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { use, useState } from "react";
import { Heading } from "@varga/design-system";
import { Card, Empty, ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";
import { relative } from "@/lib/format";
import { useSession } from "@/lib/session";

/** Un skill : ses versions, les fichiers de chacune, et les agents qui le portent. */
export default function SkillPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const { org } = useSession();
  const skill = useQuery({ queryKey: ["skill", org, slug], queryFn: () => api.skill(org, slug) });
  const [choisie, setChoisie] = useState<number | null>(null);
  const numero = choisie ?? skill.data?.latest_version ?? null;
  const version = useQuery({
    queryKey: ["skill-version", org, slug, numero],
    queryFn: () => api.skillVersion(org, slug, numero ?? 1),
    enabled: numero !== null,
  });
  const [fichier, setFichier] = useState<string | null>(null);
  if (skill.error) return <ErrorNote>{String(skill.error)}</ErrorNote>;
  if (!skill.data) return <Empty>reading the skill…</Empty>;
  const fichiers = Object.keys(version.data?.files ?? {}).sort();
  const montre = fichier && fichiers.includes(fichier) ? fichier : (fichiers.find((f) => f === "SKILL.md") ?? fichiers[0]);
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-baseline gap-3">
        <Link href="/skills" className="text-sm text-ink-muted hover:underline">
          skills
        </Link>
        <span aria-hidden className="text-ink-muted">
          /
        </span>
        <Heading as="h1" size="lg">
          {skill.data.slug}
        </Heading>
        <span className="text-sm text-ink-muted">v{skill.data.latest_version}</span>
      </div>
      {skill.data.description && <p className="max-w-3xl text-sm text-ink-muted">{skill.data.description}</p>}
      <Card
        title="files"
        action={
          (skill.data.versions ?? []).length > 1 ? (
            <label className="flex items-center gap-2 text-xs">
              <span className="text-ink-muted">version</span>
              <select
                aria-label="version to see"
                className="rounded border border-line bg-surface px-2 py-1"
                value={numero ?? ""}
                onChange={(e) => setChoisie(Number(e.target.value))}
              >
                {[...(skill.data.versions ?? [])]
                  .sort((a, b) => b.version - a.version)
                  .map((v) => (
                    <option key={v.version} value={v.version}>
                      v{v.version}
                    </option>
                  ))}
              </select>
            </label>
          ) : undefined
        }
      >
        {!version.data ? (
          <p className="text-sm text-ink-muted">reading the files…</p>
        ) : (
          <div className="grid gap-4 md:grid-cols-[14rem_1fr]" data-testid="fichiers">
            <ul className="space-y-1 text-xs">
              {fichiers.map((nom) => (
                <li key={nom}>
                  <button
                    type="button"
                    aria-current={nom === montre ? "true" : undefined}
                    className={`text-left hover:underline ${nom === montre ? "font-semibold" : ""}`}
                    onClick={() => setFichier(nom)}
                  >
                    {nom}
                  </button>
                </li>
              ))}
            </ul>
            <div className="space-y-2">
              <p className="text-xs text-ink-muted">
                {version.data.digest.slice(0, 23)}…
                {version.data.created_by ? ` · ${version.data.created_by}` : ""}
                {version.data.created_at ? ` · ${relative(version.data.created_at)}` : ""}
              </p>
              {montre && (
                <pre aria-label={montre} className="whitespace-pre-wrap break-words rounded bg-surface-muted p-3 text-xs">
                  {version.data.files?.[montre]}
                </pre>
              )}
            </div>
          </div>
        )}
      </Card>
      <Card title="carried by">
        {(skill.data.used_by ?? []).length === 0 ? (
          <p className="text-sm text-ink-muted">No agent names it yet.</p>
        ) : (
          <ul className="flex flex-wrap gap-3 text-sm">
            {(skill.data.used_by ?? []).map((agent) => (
              <li key={agent}>
                <Link href={`/agents/${agent.split("@")[0]}`} className="underline">
                  {agent}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
