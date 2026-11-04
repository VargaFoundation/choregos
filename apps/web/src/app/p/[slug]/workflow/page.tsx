// SPDX-License-Identifier: Apache-2.0
import { redirect } from "next/navigation";

/**
 * The former "the project's workflow" page: a project now carries several (ADR 0031).
 * The links that led to it (bookmarks, docs) land on the list.
 */
export default async function FormerWorkflow({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  redirect(`/p/${slug}/workflows`);
}
