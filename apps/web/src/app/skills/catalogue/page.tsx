// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { CatalogueCard, CatalogueGrid } from "@/components/catalogue/catalogue-grid";
import { CatalogueFacets } from "@/components/catalogue/facets";
import { InstallDrawer } from "@/components/catalogue/install-drawer";
import { RiskBadge } from "@/components/skills/gate";
import { SkillTabs } from "@/components/skills/onglets";
import { Badge, Button, ErrorNote, EtatDeLecture, Eyebrow, Heading } from "@/components/ui";
import { api } from "@/lib/api";
import { categoriesOf, NO_FILTERS, stars, type CatalogueFilters } from "@/lib/catalogue";
import { confirmationLines, filterSkillEntries, installAction } from "@/lib/skill-catalogue";
import { useSession } from "@/lib/session";
import type { SkillCatalogueEntry } from "@/lib/types";

const TOGGLES = [
  { key: "bundled", label: "Bundled only" },
  { key: "permissive", label: "Permissive licences only" },
  { key: "lowrisk", label: "Hide high risk" },
];

/**
 * The catalogue of default skills (ADR 0065): the most used skills of public repositories, at pinned
 * commits. An entry is an offer. Installing one publishes an ordinary skill through the supply-chain
 * gate, which runs again: the risk shown is what it said when the catalogue was generated.
 */
export default function SkillCataloguePage() {
  const { org } = useSession();
  const catalogue = useQuery({ queryKey: ["skill-catalogue", org], queryFn: () => api.skillCatalogue(org) });
  const [filters, setFilters] = useState<CatalogueFilters>(NO_FILTERS);
  const [installing, setInstalling] = useState<SkillCatalogueEntry | null>(null);
  const entries = catalogue.data?.entries ?? [];
  const shown = filterSkillEntries(entries, filters);
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <Eyebrow>What agents know how to do</Eyebrow>
        <Heading as="h1" size="xl">
          Skills
        </Heading>
        <SkillTabs active="catalogue" />
      </div>
      <p className="max-w-3xl text-sm text-ink-muted">
        Default skills from public repositories, each pinned to a commit and under an open licence. Installing one adds
        it to the library; it is assessed again on the way in, and a high-risk one waits for a second administrator.
        Nothing is installed for you and nothing follows upstream.
      </p>
      {catalogue.data && (
        <p className="text-xs text-ink-muted" data-testid="catalogue-version">
          Catalogue {catalogue.data.meta.catalogue_version}, generated {catalogue.data.meta.generated_at.slice(0, 10)}.
          Stars are a snapshot.
        </p>
      )}
      <EtatDeLecture lecture={catalogue} quoi="the catalogue" />
      {catalogue.data && (
        <>
          <CatalogueFacets
            filters={filters}
            onChange={setFilters}
            categories={categoriesOf(entries)}
            toggles={TOGGLES}
            shown={shown.length}
            total={entries.length}
            noun="skills"
          />
          <CatalogueGrid
            items={shown}
            keyOf={(entry) => entry.id}
            empty="Loosen a filter, or search for another word."
            render={(entry) => <Entry entry={entry} onInstall={() => setInstalling(entry)} />}
          />
        </>
      )}
      {installing && (
        <InstallDrawer title={installing.title} onClose={() => setInstalling(null)}>
          <Confirm org={org} entry={installing} onDone={() => setInstalling(null)} />
        </InstallDrawer>
      )}
    </div>
  );
}

function Entry({ entry, onInstall }: { entry: SkillCatalogueEntry; onInstall: () => void }) {
  const action = installAction(entry);
  const stateNote =
    entry.state === "blocked"
      ? entry.blocked_reason
      : entry.state === "installed"
        ? `Installed, version ${entry.installed_version}.`
        : entry.state === "update"
          ? `Version ${entry.installed_version} is installed; the catalogue pins a newer one.`
          : entry.state === "pending_review"
            ? "Installed, waiting for a second administrator to approve it."
            : null;
  return (
    <CatalogueCard
      id={entry.id}
      title={entry.title}
      publisher={entry.publisher}
      description={entry.description}
      badges={
        <>
          <RiskBadge risk={entry.risk} reviewStatus={entry.state === "pending_review" ? "pending_review" : undefined} />
          <Badge tone={entry.licence_class === "permissive" ? "ok" : "warn"}>
            {entry.licence_class === "permissive" ? "Permissive" : "Copyleft"} · {entry.licence}
          </Badge>
          <Badge tone="neutral">{entry.delivery === "bundled" ? "Bundled" : "From git"}</Badge>
          {entry.state === "installed" && <Badge tone="ok">Installed</Badge>}
          {entry.state === "update" && <Badge tone="warn">Update available</Badge>}
        </>
      }
      facts={
        <>
          {entry.signals.stars != null && (
            <p>
              <span aria-hidden>★</span> {stars(entry.signals.stars)} stars as of {entry.signals.stars_as_of}
            </p>
          )}
          <p>
            {entry.files} {entry.files === 1 ? "file" : "files"}
          </p>
          {entry.delivery === "git" && entry.egress === "undeclared" && (
            <p className="text-waiting-ink">codeload.github.com is not declared reachable from the API.</p>
          )}
        </>
      }
      footer={
        <>
          <Button
            tone="accent"
            size="sm"
            disabled={action.disabled}
            aria-describedby={stateNote ? `note-${entry.id}` : undefined}
            onClick={onInstall}
          >
            {action.label}
          </Button>
          {stateNote && (
            <p id={`note-${entry.id}`} className="text-xs text-ink-muted">
              {stateNote}
            </p>
          )}
        </>
      }
    />
  );
}

/** What the person takes on, then the install; on success the skill's own page, with its provenance. */
function Confirm({ org, entry, onDone }: { org: string; entry: SkillCatalogueEntry; onDone: () => void }) {
  const router = useRouter();
  const client = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function install() {
    setError(null);
    setBusy(true);
    try {
      const skill = await api.installSkillCatalogueEntry(org, entry.id);
      await client.invalidateQueries({ queryKey: ["skills", org] });
      await client.invalidateQueries({ queryKey: ["skill-catalogue", org] });
      onDone();
      router.push(`/skills/${skill.slug}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "refused");
      setBusy(false);
    }
  }
  return (
    <div className="space-y-4 text-sm">
      <p className="text-ink-muted" data-donnee>
        {entry.description}
      </p>
      <ul className="list-disc space-y-1 pl-5" data-testid="confirmation">
        {confirmationLines(entry).map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      {error && <ErrorNote>{error}</ErrorNote>}
      <div className="flex gap-2">
        <Button tone="accent" disabled={busy} onClick={() => void install()}>
          {busy ? "Installing…" : entry.state === "update" ? "Update" : "Install"}
        </Button>
        <Button onClick={onDone}>Cancel</Button>
      </div>
    </div>
  );
}
