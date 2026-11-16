// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { Empty, Field, Input, ReadState, Select } from "@/components/ui";
import { api } from "@/lib/api";
import { outpostConfigProblems, presence, refusalOf, stateLabel, timeoutBounds } from "@/lib/outposts";
import type { Outpost } from "@/lib/types";
import { RefusalNote } from "./refusal";

type Config = Record<string, unknown>;

/** Why an outpost cannot take a connector, said in its option; `null` when it can. */
function cannotServe(outpost: Outpost): string | null {
  if (!outpost.capabilities.includes("tools")) return "not enabled for tools";
  if (outpost.status === "revoked") return "revoked";
  return null;
}

function optionLabel(outpost: Outpost): string {
  const name = outpost.display_name !== outpost.slug ? `${outpost.display_name} (${outpost.slug})` : outpost.slug;
  const why = cannotServe(outpost);
  if (why) return `${name} — ${why}`;
  const status = outpost.status === "active" ? "" : `${stateLabel(outpost.status).toLowerCase()}, `;
  return `${name} — ${status}${presence(outpost).label.toLowerCase()}`;
}

/**
 * The configuration of a connector at an outpost (ADR 0149 §1): an outpost of the organisation, an
 * endpoint alias of that outpost, a timeout. The outpost is chosen among the organisation's outposts,
 * never typed; the alias among what its instances announce, and typed only while none announces one
 * (an administrator may declare before the outpost's operator has an instance running). There is no URL
 * and no secret: the server's address and its credential live in the outpost's policy file.
 *
 * `errors` are the API's, by field (its 422 names the field); the form adds its own courtesy checks,
 * and the API stays the judge.
 */
export function OutpostConnectorFields({
  org,
  schema,
  value,
  onChange,
  errors = {},
  disabled,
}: {
  org: string;
  /** The type's declared schema: where the timeout's bounds are read. */
  schema: unknown;
  value: Config;
  onChange: (next: Config) => void;
  errors?: Record<string, string>;
  disabled?: boolean;
}) {
  const outposts = useQuery({ queryKey: ["outposts", org], queryFn: () => api.outposts(org) });
  const items = outposts.data?.items ?? [];
  const chosen = typeof value.outpost === "string" ? value.outpost : "";
  const outpost = items.find((o) => o.slug === chosen);
  const endpoint = typeof value.endpoint === "string" ? value.endpoint : "";
  const announced = outpost?.endpoints ?? [];
  const local = outpostConfigProblems(value, schema);
  const { min, max, fallback } = timeoutBounds(schema);
  const set = (field: string, next: unknown) => {
    const copy = { ...value };
    if (next === undefined || next === "") delete copy[field];
    else copy[field] = next;
    onChange(copy);
  };

  if (outposts.error) return <RefusalNote refusal={refusalOf(outposts.error)} />;
  if (!outposts.data) return <ReadState lecture={outposts} quoi="the outposts" />;
  if (items.length === 0) {
    return (
      <Empty
        title="No outpost yet"
        action={
          <Link href="/admin/outposts" className="text-sm underline">
            Create one in Outposts
          </Link>
        }
      >
        A connector at an outpost routes to an outpost of {org}: create the outpost first.
      </Empty>
    );
  }
  return (
    <div className="space-y-3" data-testid="outpost-connector-fields">
      <p className="text-xs text-ink-muted">
        No URL and no secret: the server&apos;s address and its credential are written in the outpost&apos;s policy file,
        never in Choregos.
      </p>
      <div className="grid gap-3 md:grid-cols-2 [&>*]:min-w-0">
        <Field label="Outpost" hint="An outpost of this organisation, enabled for tools." error={errors.outpost}>
          {(props) => (
            <Select
              {...props}
              value={chosen}
              disabled={disabled}
              onChange={(event) => {
                const next = items.find((o) => o.slug === event.target.value);
                // An alias belongs to its outpost: one the new outpost does not announce is cleared.
                const kept = next && next.endpoints.includes(endpoint) ? endpoint : undefined;
                const copy: Config = { ...value, outpost: event.target.value || undefined, endpoint: kept };
                for (const key of ["outpost", "endpoint"]) if (copy[key] === undefined) delete copy[key];
                onChange(copy);
              }}
            >
              <option value="">—</option>
              {items.map((o) => (
                <option key={o.slug} value={o.slug} disabled={cannotServe(o) !== null}>
                  {optionLabel(o)}
                </option>
              ))}
              {chosen && !outpost && <option value={chosen}>{chosen} — not an outpost of {org}</option>}
            </Select>
          )}
        </Field>
        {announced.length > 0 ? (
          <Field
            label="Endpoint"
            hint={`The aliases the instances of ${outpost?.slug} announce, from its policy file.`}
            error={errors.endpoint ?? local.endpoint}
          >
            {(props) => (
              <Select {...props} value={endpoint} disabled={disabled} onChange={(event) => set("endpoint", event.target.value)}>
                <option value="">—</option>
                {announced.map((alias) => (
                  <option key={alias} value={alias}>
                    {alias}
                  </option>
                ))}
                {endpoint && !announced.includes(endpoint) && <option value={endpoint}>{endpoint} — not announced now</option>}
              </Select>
            )}
          </Field>
        ) : (
          <Field
            label="Endpoint"
            hint={
              outpost
                ? `No instance of ${outpost.slug} announces an endpoint yet: type the alias as its policy file writes it.`
                : "Choose the outpost first: its instances announce the aliases to choose from."
            }
            error={errors.endpoint ?? local.endpoint}
          >
            {(props) => (
              <Input
                {...props}
                value={endpoint}
                disabled={disabled || !outpost}
                autoComplete="off"
                placeholder="tracker"
                onChange={(event) => set("endpoint", event.target.value.trim())}
              />
            )}
          </Field>
        )}
        <Field
          label="Timeout (seconds)"
          hint={`${min !== undefined && max !== undefined ? `Between ${min} and ${max}` : "Seconds"}${
            fallback !== undefined ? `, ${fallback} when left empty` : ""
          }. It bounds the whole call: the wait for an instance to take it, then the server's answer.`}
          error={errors.timeout_s ?? local.timeout_s}
        >
          {(props) => (
            <Input
              {...props}
              type="number"
              min={min}
              max={max}
              placeholder={fallback !== undefined ? String(fallback) : undefined}
              value={typeof value.timeout_s === "number" ? String(value.timeout_s) : ""}
              disabled={disabled}
              onChange={(event) => set("timeout_s", event.target.value === "" ? undefined : Number(event.target.value))}
            />
          )}
        </Field>
      </div>
    </div>
  );
}
