// SPDX-License-Identifier: Apache-2.0
import Link from "next/link";
import { Tabs, tabClasses } from "@/components/ui";

/** The two views of the skills of an organisation: what it has, and what it can add (ADR 0065). */
export function SkillTabs({ active }: { active: "library" | "catalogue" }) {
  return (
    <Tabs aria-label="Skill views">
      <Link href="/skills" className={tabClasses(active === "library")} aria-current={active === "library" ? "page" : undefined}>
        Library
      </Link>
      <Link
        href="/skills/catalogue"
        className={tabClasses(active === "catalogue")}
        aria-current={active === "catalogue" ? "page" : undefined}
      >
        Catalogue
      </Link>
    </Tabs>
  );
}
