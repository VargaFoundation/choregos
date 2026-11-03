// SPDX-License-Identifier: Apache-2.0
"use client";

import type { ReactNode } from "react";
import { A2aSection } from "@/components/project-settings/a2a-section";
import { AgentsModelsSection } from "@/components/project-settings/agents-models-section";
import { ConnectionsSection } from "@/components/project-settings/connections-section";
import { DangerZoneSection } from "@/components/project-settings/danger-zone-section";
import { GeneralSection } from "@/components/project-settings/general-section";
import { GuardrailsSection } from "@/components/project-settings/guardrails-section";
import { KnowledgeMemorySection } from "@/components/project-settings/knowledge-memory-section";
import { NotificationsSection } from "@/components/project-settings/notifications-section";
import { PeopleSection } from "@/components/project-settings/people-section";
import { SettingsSection } from "@/components/project-settings/section";
import { ToolsSection } from "@/components/project-settings/tools-section";
import { ProjectNetworkCard } from "@/components/project/network-card";

/** The section components, by anchor: a group page composes them, one file each, as before. */
export function SettingsSectionById({ id, slug }: { id: string; slug: string }): ReactNode {
  switch (id) {
    case "general":
      return <GeneralSection slug={slug} />;
    case "connections":
      return <ConnectionsSection slug={slug} />;
    case "notifications":
      return <NotificationsSection slug={slug} />;
    case "agents-models":
      return <AgentsModelsSection slug={slug} />;
    case "tools":
      return <ToolsSection slug={slug} />;
    case "a2a":
      return <A2aSection slug={slug} />;
    case "knowledge-memory":
      return <KnowledgeMemorySection slug={slug} />;
    case "guardrails":
      return <GuardrailsSection slug={slug} />;
    case "network":
      return (
        <SettingsSection
          id="network"
          title="Network"
          lead="The hosts this project's runs may reach, within what the organisation allows."
        >
          <ProjectNetworkCard slug={slug} />
        </SettingsSection>
      );
    case "people":
      return <PeopleSection slug={slug} />;
    case "danger-zone":
      return <DangerZoneSection slug={slug} />;
    default:
      return null;
  }
}
