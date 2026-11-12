// SPDX-License-Identifier: Apache-2.0
/** Reading what an assistant said: its citations, and the accounts it asks the person to connect. */

/** `[document title, version]`: how the platform asks the assistant to cite a knowledge passage. */
export interface Citation {
  title: string;
  version: string;
}

export function citationsOf(text: string): Citation[] {
  const found = new Map<string, Citation>();
  for (const match of text.matchAll(/\[([^\][\n]{1,120}?),\s*(?:v|version\s*)?(\d+)\]/gi)) {
    const citation = { title: (match[1] ?? "").trim(), version: match[2] ?? "" };
    found.set(`${citation.title}@${citation.version}`, citation);
  }
  return [...found.values()];
}

/** An account to connect first: the console page that starts the consent. */
export interface ConnectLink {
  connector: string;
  org: string;
  href: string;
}

/** The consent links an answer carries (`account_not_connected` hands the person the console's connect URL). */
export function connectLinksOf(text: string, extra: { connect_url?: string }[] = []): ConnectLink[] {
  const urls = [...text.matchAll(/\S*\/me\/connected-accounts\/[a-z0-9._-]+\/connect\?org=[A-Za-z0-9._-]+/g)].map(
    (m) => m[0],
  );
  for (const e of extra) if (e.connect_url) urls.push(e.connect_url);
  const links = new Map<string, ConnectLink>();
  for (const url of urls) {
    const match = /\/me\/connected-accounts\/([a-z0-9._-]+)\/connect\?org=([A-Za-z0-9._-]+)/.exec(url);
    if (!match) continue;
    const link = { connector: match[1] ?? "", org: match[2] ?? "", href: `/me/connected-accounts/${match[1]}/connect?org=${match[2]}` };
    links.set(link.href, link);
  }
  return [...links.values()];
}

/** The proposals of a memory space that wait for a decision. */
export function isProposed(status: string): boolean {
  return status === "proposed";
}

/**
 * The installation's id, which the cost report's and the Activity list's `assistant` filters take
 * (ADR 0141 §1), or `null` when the answer does not say it.
 *
 * TODO(contract) #1657: `Assistant` carries the slug, not the id; read when the API sends it, so that a
 * page that cannot narrow says so rather than show the organisation's whole spend as the assistant's.
 */
export function installationId(assistant: { slug: string }): string | null {
  const id = (assistant as { id?: unknown }).id;
  return typeof id === "string" && id !== "" ? id : null;
}
