// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { InstanceIdentityCard } from "@/components/admin/instance-identity";
import { SectionRendue } from "@/components/admin/section";
import { isIdentitySection } from "@/components/coquille/navigation";
import { EtatDeLecture, Heading, Lead } from "@/components/ui";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";
import { withoutCoreRows } from "@/lib/identity";

/**
 * Identity, in one place (S44-04): how people sign in to the instance (the core's read-only card,
 * platform administrators only) and the identity sections a plugin declares (an icon of
 * `IDENTITY_ICONS`) — single sign-on, directory provisioning. They were three entries: this card on
 * the Platform page, and two plugin sections, one of them showing the same rows as the card.
 */
export default function IdentityPage() {
  const { org } = useSession();
  const sections = useQuery({
    queryKey: ["admin-sections", org],
    queryFn: () => api.adminSections(org),
  });
  const identity = (sections.data ?? [])
    .filter(isIdentitySection)
    .map(withoutCoreRows)
    .filter((section) => section.blocks.length > 0);
  return (
    <div className="space-y-6">
      <Lead>
        Who the people of {org} are and how they sign in. The instance&apos;s
        sign-in is set by the deployment; what an edition adds (single sign-on,
        directory provisioning) is set here.
      </Lead>
      <InstanceIdentityCard />
      <EtatDeLecture lecture={sections} quoi="the identity settings" />
      {identity.map((section) => (
        <section
          key={section.id}
          id={section.id}
          className="space-y-3"
          aria-labelledby={`${section.id}-title`}
        >
          <Heading as="h2" size="lg" id={`${section.id}-title`}>
            {section.title}
          </Heading>
          <SectionRendue section={section} org={org} />
        </section>
      ))}
    </div>
  );
}
