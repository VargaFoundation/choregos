// SPDX-License-Identifier: Apache-2.0
/**
 * The setup procedures on the mock API: the real files, rendered with example addresses by
 * `tools/procedures/export_docs.py --mocks` (a test refuses a stale copy). The lookup follows the
 * API's: a preset before the generic procedure.
 */

import { ApiError } from "@/lib/api";
import type { Procedure } from "@/lib/types";
import raw from "./procedures.json";

const PROCEDURES = raw as unknown as Procedure[];

function summary(procedure: Procedure) {
  const { id, title, version, audience, applies_to, link } = procedure;
  return { id, title, version, audience, applies_to, link };
}

function best(target: string, cardHost: string | null, preset: string | null): Procedure | undefined {
  const [key, ...rest] = target.split(":");
  const value = rest.join(":");
  const candidates = PROCEDURES.filter((p) => p.applies_to[key!] === value);
  const preferred = candidates.find((p) => {
    const extra = Object.entries(p.applies_to).filter(([k]) => k === "card_host" || k === "preset");
    return (
      extra.length > 0 &&
      extra.every(([k, v]) => (k === "card_host" ? cardHost === v : preset === v))
    );
  });
  return preferred ?? candidates.find((p) => !("card_host" in p.applies_to) && !("preset" in p.applies_to));
}

/** `undefined` when the path is not a procedure route. */
export function proceduresApi(method: string, path: string): unknown {
  if (method !== "GET") return undefined;
  const [route, query = ""] = path.split("?");
  if (route === "/procedures") {
    const params = new URLSearchParams(query);
    const target = params.get("for");
    if (!target) return PROCEDURES.map(summary);
    const found = best(target, params.get("card_host"), params.get("preset"));
    return found ? [summary(found)] : [];
  }
  const one = /^\/procedures\/([^/]+)$/.exec(route ?? "");
  if (one) {
    const found = PROCEDURES.find((p) => p.id === decodeURIComponent(one[1]!));
    if (!found) throw new ApiError(404, { title: "Not found" });
    return structuredClone(found);
  }
  return undefined;
}
