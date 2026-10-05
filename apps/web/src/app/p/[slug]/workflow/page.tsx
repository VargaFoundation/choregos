// SPDX-License-Identifier: Apache-2.0
import { redirect } from "next/navigation";

/**
 * L'ancienne page « le workflow du projet » : un projet en porte désormais plusieurs (ADR 0031).
 * Les liens qui y menaient — marque-pages, docs — arrivent sur la liste.
 */
export default async function AncienWorkflow({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  redirect(`/p/${slug}/workflows`);
}
