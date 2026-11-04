// SPDX-License-Identifier: Apache-2.0
import Link from "next/link";
import { Tabs, tabClasses } from "@/components/ui";

/**
 * The two views of the skills of an organisation: what it has, and what it can add (ADR 0065). The
 * second one is the library, at `/skills/library` (S55-15: "catalogue" read as French); what the
 * organisation has is therefore "Installed", as for the connectors.
 */
export function SkillTabs({ active }: { active: "installed" | "library" }) {
  return (
    <Tabs aria-label="Skill views">
      <Link href="/skills" className={tabClasses(active === "installed")} aria-current={active === "installed" ? "page" : undefined}>
        Installed
      </Link>
      <Link
        href="/skills/library"
        className={tabClasses(active === "library")}
        aria-current={active === "library" ? "page" : undefined}
      >
        Library
      </Link>
    </Tabs>
  );
}
