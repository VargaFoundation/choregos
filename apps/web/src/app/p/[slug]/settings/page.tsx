// SPDX-License-Identifier: Apache-2.0
"use client";

import { use } from "react";
import { AgentsModelsSection } from "@/components/project-settings/agents-models-section";
import { ConnectionsSection } from "@/components/project-settings/connections-section";
import { DangerZoneSection } from "@/components/project-settings/danger-zone-section";
import { GeneralSection } from "@/components/project-settings/general-section";
import { GuardrailsSection } from "@/components/project-settings/guardrails-section";
import { KnowledgeMemorySection } from "@/components/project-settings/knowledge-memory-section";
import { NotificationsSection } from "@/components/project-settings/notifications-section";
import { PeopleSection } from "@/components/project-settings/people-section";
import { ReadinessPanel } from "@/components/project-settings/readiness-panel";
import { SettingsNav } from "@/components/project-settings/settings-nav";
import { ToolsSection } from "@/components/project-settings/tools-section";

/** The sections of the page, in reading order: what the project is connected to first. */
const SECTIONS = [
  { id: "readiness", label: "Readiness" },
  { id: "general", label: "General" },
  { id: "connections", label: "Connections" },
  { id: "agents-models", label: "Agents & models" },
  { id: "guardrails", label: "Guardrails" },
  { id: "notifications", label: "Notifications" },
  { id: "tools", label: "Tools" },
  { id: "people", label: "People" },
  { id: "knowledge-memory", label: "Knowledge & memory" },
  { id: "danger-zone", label: "Danger zone" },
];

/**
 * A project's settings, and they are edited here (S40-03b). The page only COMPOSES sections, one file
 * each under `components/project-settings/`, so a later section is added without touching the others:
 * the readiness panel (S40-06c) sits on top and every field of the project's configuration has a section.
 *
 * Before: one two-column grid of unrelated cards with no heading, no way to tell what is required from
 * what is optional, a raw 380 px YAML editor, and a connector row printing the raw kind, the raw state
 * and no icon.
 */
export default function SettingsPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  return (
    <div className="gap-8 space-y-4 lg:grid lg:grid-cols-[11rem_minmax(0,1fr)] lg:space-y-0">
      <SettingsNav sections={SECTIONS} />
      <div className="min-w-0 space-y-10">
        <section id="readiness" aria-label="Readiness" className="scroll-mt-4 empty:hidden">
          <ReadinessPanel slug={slug} />
        </section>
        <GeneralSection slug={slug} />
        <ConnectionsSection slug={slug} />
        <AgentsModelsSection slug={slug} />
        <GuardrailsSection slug={slug} />
        <NotificationsSection slug={slug} />
        <ToolsSection slug={slug} />
        <PeopleSection slug={slug} />
        <KnowledgeMemorySection slug={slug} />
        <DangerZoneSection slug={slug} />
      </div>
    </div>
  );
}
