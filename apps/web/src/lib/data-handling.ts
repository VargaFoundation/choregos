// SPDX-License-Identifier: Apache-2.0
import declaredTable from "./web-search-declared.json";

/**
 * What a `web_search` connector says its provider does with a query (ADR 0083 §3). `null`/absent is
 * unknown, and an unknown value fails every bound that reads it.
 */
export type DataHandling = {
  query_retention_days?: number | null;
  query_reuse?: boolean | null;
  region?: string | null;
  terms_ref?: string | null;
};

const FIELDS = ["query_retention_days", "query_reuse", "region", "terms_ref"];

/**
 * What Choregos declares for a backend type, mirrored from `choregos_adapters.web_search.declared`
 * (the API does not serve it; `tests/docs/test_web_search_declared_mirror.py` refuses a difference).
 * A type not in the table declares nothing: everything unknown.
 */
export function declaredHandling(type: string): DataHandling {
  return (declaredTable as Record<string, DataHandling>)[type] ?? {};
}

const isSet = (value: unknown) => value !== undefined && value !== null && value !== "";

/**
 * Why the API's `stricter` (packages/core web_search.py) would refuse this override, one line each;
 * empty: it would accept it. The API stays the authority: this only stops a save that is known to fail.
 */
export function dataHandlingProblems(type: string, override: unknown): string[] {
  if (!override || typeof override !== "object" || Array.isArray(override)) return [];
  const given = Object.fromEntries(Object.entries(override).filter(([, value]) => isSet(value))) as Record<
    string,
    unknown
  >;
  const unknown = Object.keys(given).filter((key) => !FIELDS.includes(key));
  if (unknown.length > 0) return [`unknown field(s) ${unknown.join(", ")}`];
  if (Object.keys(given).length === 0) return [];
  const declared = declaredHandling(type);
  const problems: string[] = [];
  if (typeof given.terms_ref !== "string" || given.terms_ref.trim() === "") {
    problems.push("a declaration must name the terms it comes from (terms_ref)");
  }
  const days = given.query_retention_days;
  if ("query_retention_days" in given) {
    if (typeof days !== "number" || !Number.isInteger(days) || days < 0) {
      problems.push("retention is a number of days, 0 or more");
    } else if (declared.query_retention_days != null && days > declared.query_retention_days) {
      problems.push(
        `${days} days is longer than the backend's declared ${declared.query_retention_days}: a declaration can only be stricter`,
      );
    }
  }
  if ("query_reuse" in given) {
    if (typeof given.query_reuse !== "boolean") problems.push("reuse is yes or no");
    else if (given.query_reuse && declared.query_reuse === false) {
      problems.push("the backend declares no reuse: yes is wider");
    }
  }
  if ("region" in given) {
    if (typeof given.region !== "string" || given.region.trim() === "") problems.push("region is a name");
    else if (declared.region != null && given.region !== declared.region) {
      problems.push(`the backend declares \`${declared.region}\`: only an unknown region can be declared`);
    }
  }
  return problems;
}
