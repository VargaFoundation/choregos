// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button, Card, ErrorNote, Field, Input, Select, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { type CeilingNetwork, parseHosts } from "@/lib/network";

type AllowMode = "nocap" | "any" | "listed";

function modeOf(network: CeilingNetwork): AllowMode {
  if (network.allow_domains == null) return "nocap";
  return network.allow_domains.length === 1 && network.allow_domains[0] === "*" ? "any" : "listed";
}

const toText = (hosts: string[] | null | undefined) => (hosts ?? []).join("\n");
const toNumber = (text: string): number | null => {
  const n = Number.parseInt(text, 10);
  return Number.isFinite(n) && n >= 1 ? n : null;
};

/**
 * The organisation's network access (ADR 0070): the widest set of hosts a project or an agent may
 * allow, the hosts no run may reach, how often one host may be called, and who to contact. It is the
 * `network` section of the policy ceiling: saving reads the whole ceiling again and replaces only that
 * section, so this card and the JSON card never erase each other.
 */
export function NetworkCeilingCard({ org, canEdit }: { org: string; canEdit: boolean }) {
  const client = useQueryClient();
  const ceiling = useQuery({ queryKey: ["policy-ceiling", org], queryFn: () => api.policyCeiling(org) });
  const stored = ((ceiling.data?.ceiling ?? {}) as { network?: CeilingNetwork }).network ?? {};
  const [mode, setMode] = useState<AllowMode | null>(null);
  const [allow, setAllow] = useState<string | null>(null);
  const [deny, setDeny] = useState<string | null>(null);
  const [perHost, setPerHost] = useState<string | null>(null);
  const [perRun, setPerRun] = useState<string | null>(null);
  const [webTools, setWebTools] = useState<string | null>(null);
  const [contact, setContact] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const shownMode = mode ?? modeOf(stored);
  const shownAllow = allow ?? (shownMode === "listed" ? toText(stored.allow_domains) : "");
  const shownDeny = deny ?? toText(stored.deny_domains);
  const shownPerHost = perHost ?? String(stored.per_host_per_minute ?? "");
  const shownPerRun = perRun ?? String(stored.per_run_per_minute ?? "");
  const shownTools = webTools ?? stored.builtin_web_tools ?? "";
  const shownContact = contact ?? stored.user_agent_contact ?? "";

  async function save() {
    setError(null);
    setSaved(false);
    const allowed = shownMode === "listed" ? parseHosts(shownAllow) : { hosts: [], problems: [] };
    const denied = parseHosts(shownDeny);
    const problems = [...allowed.problems, ...denied.problems];
    if (problems.length) return setError(problems.join(" "));
    if (shownMode === "listed" && allowed.hosts.length === 0)
      return setError("Name at least one allowed host, or choose “No cap” or “Any public host”.");
    const network: CeilingNetwork = {
      allow_domains: shownMode === "nocap" ? null : shownMode === "any" ? ["*"] : allowed.hosts,
      deny_domains: denied.hosts,
      per_host_per_minute: toNumber(shownPerHost),
      per_run_per_minute: toNumber(shownPerRun),
      builtin_web_tools: shownTools === "allow" || shownTools === "deny" ? shownTools : null,
      user_agent_contact: shownContact.trim() || null,
    };
    try {
      // Read the ceiling again: the JSON card, or another administrator, may have changed the rest.
      const fresh = await api.policyCeiling(org);
      await api.putPolicyCeiling(org, { ...(fresh.ceiling ?? {}), network });
      setMode(null);
      setAllow(null);
      setDeny(null);
      setPerHost(null);
      setPerRun(null);
      setWebTools(null);
      setContact(null);
      setSaved(true);
      await client.invalidateQueries({ queryKey: ["policy-ceiling", org] });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "refused");
    }
  }

  const readOnly = !canEdit;
  return (
    <Card title="Network access">
      <div className="space-y-3 text-sm">
        <p className="text-ink-muted">
          What the agents of {org} may reach on the internet. A project and an agent can only narrow it. A host name covers its
          subdomains; <code>*.example.com</code> covers the subdomains only.
        </p>
        <fieldset className="space-y-1.5" disabled={readOnly}>
          <legend className="text-xs font-medium text-ink-muted">Allowed hosts</legend>
          {(
            [
              ["nocap", "No cap", "Projects choose their own hosts."],
              ["any", "Any public host", "Every public host name; the denied hosts below still apply."],
              ["listed", "Only these hosts", "Projects and agents stay inside this list."],
            ] as const
          ).map(([value, label, hint]) => (
            <label key={value} className="flex items-start gap-2">
              <input type="radio" name="network-allow" className="mt-1" checked={shownMode === value} onChange={() => setMode(value)} />
              <span>
                {label} <span className="text-xs text-ink-muted">— {hint}</span>
              </span>
            </label>
          ))}
        </fieldset>
        {shownMode === "listed" && (
          <Field label="Allowed hosts" hint="One per line.">
            {(props) => <Textarea {...props} rows={4} value={shownAllow} readOnly={readOnly} onChange={(e) => setAllow(e.target.value)} />}
          </Field>
        )}
        <Field label="Denied hosts" hint="One per line. No run reaches them, whatever a project or an agent says.">
          {(props) => <Textarea {...props} rows={3} value={shownDeny} readOnly={readOnly} onChange={(e) => setDeny(e.target.value)} />}
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Requests per minute, per host" hint="Counted across the organisation. Empty: no cap.">
            {(props) => <Input {...props} inputMode="numeric" value={shownPerHost} readOnly={readOnly} onChange={(e) => setPerHost(e.target.value)} />}
          </Field>
          <Field label="Requests per minute, per run" hint="Empty: no cap.">
            {(props) => <Input {...props} inputMode="numeric" value={shownPerRun} readOnly={readOnly} onChange={(e) => setPerRun(e.target.value)} />}
          </Field>
          <Field label="Built-in web tools" hint="The agent CLI's own WebFetch and WebSearch.">
            {(props) => (
              <Select {...props} value={shownTools} disabled={readOnly} onChange={(e) => setWebTools(e.target.value)}>
                <option value="">Inherit (projects decide)</option>
                <option value="allow">Allow</option>
                <option value="deny">Deny</option>
              </Select>
            )}
          </Field>
          <Field label="Contact in the User-Agent" hint="An address or a page the operators of a site can reach you at.">
            {(props) => <Input {...props} value={shownContact} readOnly={readOnly} onChange={(e) => setContact(e.target.value)} placeholder="security@example.com" />}
          </Field>
        </div>
        {canEdit && (
          <Button onClick={() => void save()}>Save network access</Button>
        )}
        {saved && <p className="text-sm text-ok">Network access saved</p>}
        {error && <ErrorNote>{error}</ErrorNote>}
      </div>
    </Card>
  );
}
