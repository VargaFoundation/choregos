// SPDX-License-Identifier: Apache-2.0
"use client";

import Link from "next/link";
import { Connecteurs } from "@/components/connecteurs";
import { SettingsSection } from "@/components/project-settings/section";

/** What the project is connected to: a card per system, an icon, a state a person can read. */
export function ConnectionsSection({ slug }: { slug: string }) {
  return (
    <SettingsSection
      id="connections"
      title="Connections"
      lead={
        <>
          The outside systems this project&apos;s workflows need, and why. A secret is typed once and stored encrypted;
          the console then only shows that it is set. Systems the whole organisation shares — a directory, MCP servers —
          are declared in{" "}
          <Link href="/admin/connectors" className="underline">
            Admin › connectors
          </Link>
          .
        </>
      }
    >
      <Connecteurs slug={slug} />
    </SettingsSection>
  );
}
