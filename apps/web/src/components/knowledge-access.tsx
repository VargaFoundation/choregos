// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Badge, Card, ErrorNote, Input, Select, buttonClasses } from "@/components/ui";
import { api } from "@/lib/api";
import { GRANT_ROLES, accessLabel, grantKey, grantName, grantsBody, permissionsOf, roleHint, scopeLabel } from "@/lib/knowledge";
import type { KnowledgeBase, KnowledgeGrant } from "@/lib/types";

type Kind = KnowledgeGrant["subject_type"] | "email";

/** The list a base's grants edit: copied from the server's, replaced as a whole on save. */
function initial(base: KnowledgeBase): KnowledgeGrant[] {
  return (base.grants ?? []).map((g) => ({ ...g }));
}

/**
 * Who may do what with one knowledge base (S42-11, ADR 0081). The reach says which runs are served
 * it; the grants below give people a role on it. Every button follows the server's `can`: an
 * administrator or an owner of the owning project widens the reach and restricts the base, whoever may share edits the grants.
 */
export function KnowledgeAccess({ org, base, onChange }: { org: string; base: KnowledgeBase; onChange: () => void }) {
  const can = permissionsOf(base);
  const [draft, setDraft] = useState<KnowledgeGrant[]>(() => initial(base));
  const [seen, setSeen] = useState(JSON.stringify(base.grants ?? []));
  const [kind, setKind] = useState<Kind>("user");
  const [subject, setSubject] = useState("");
  const [role, setRole] = useState<KnowledgeGrant["role"]>("viewer");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [restricted, setRestricted] = useState(Boolean(base.restricted));
  // The list follows the server's after a save (or another person's), unless someone is editing it.
  const fromServer = JSON.stringify(base.grants ?? []);
  if (fromServer !== seen) {
    setSeen(fromServer);
    setDraft(initial(base));
  }
  const members = useQuery({ queryKey: ["members", org], queryFn: () => api.members(org), enabled: can.share && kind === "user" });
  const projects = useQuery({ queryKey: ["projects", org], queryFn: () => api.projects(org), enabled: can.share && kind === "project" });
  const dirty = JSON.stringify(grantsBody(draft)) !== JSON.stringify(grantsBody(base.grants ?? []));

  function add() {
    const value = subject.trim();
    if (!value) return;
    const grant: KnowledgeGrant = { subject_type: kind === "email" ? "user" : kind, subject: kind === "email" ? value.toLowerCase() : value, role };
    setDraft((list) => [...list.filter((g) => grantKey(g) !== grantKey(grant)), grant]);
    setSubject("");
  }
  async function save() {
    setError(null);
    setNotice(null);
    try {
      await api.putKnowledgeGrants(org, base.slug, grantsBody(draft));
      setNotice("Access saved");
      onChange();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Refused");
    }
  }
  async function saveRestricted(next: boolean) {
    setError(null);
    setRestricted(next);
    try {
      await api.updateKnowledgeBase(org, base.slug, { restricted: next });
      onChange();
    } catch (cause) {
      setRestricted(!next);
      setError(cause instanceof Error ? cause.message : "Refused");
    }
  }

  const memberRows = [...new Map((members.data ?? []).filter((m) => m.user_id).map((m) => [m.user_id, m])).values()];
  return (
    <Card title="Access">
      <div className="space-y-4 text-sm" data-testid="knowledge-access">
        <p>
          <span className="text-ink-muted">Served to runs:</span> {scopeLabel(base)}
          {base.owner_project && (
            <span className="text-ink-muted"> · owned by project {base.owner_project}, whose owners are owners of this base</span>
          )}
        </p>
        <p data-testid="knowledge-readable">
          <span className="text-ink-muted">Who reads it:</span> {accessLabel(base)}
          {base.my_role && (
            <Badge tone="neutral" className="ml-2">
              you: {base.my_role}
            </Badge>
          )}
        </p>
        {can.widen ? (
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={restricted} onChange={(event) => void saveRestricted(event.target.checked)} />
            Restricted: people read it only through a grant
          </label>
        ) : (
          <p className="text-ink-muted">Only an organisation administrator, or an owner of the project that owns this base, changes who it is served to or restricts it.</p>
        )}
        {can.share ? (
          <div className="space-y-3">
            {draft.length === 0 ? (
              <p className="text-ink-muted" data-testid="knowledge-no-grants">
                No grant yet: the reach above decides who reads it, and its owners feed it.
              </p>
            ) : (
              <table className="w-full" data-testid="knowledge-grants">
                <thead className="text-left text-xs text-ink-muted">
                  <tr>
                    <th className="py-1 font-normal">Who</th>
                    <th className="py-1 font-normal">Role</th>
                    <th className="py-1 font-normal" />
                  </tr>
                </thead>
                <tbody>
                  {draft.map((grant) => (
                    <tr key={grantKey(grant)} className="border-t border-line">
                      <td className="py-2">
                        <span className="font-medium">{grantName(grant)}</span>{" "}
                        <span className="text-xs text-ink-muted">{grant.subject_type}</span>
                        {grant.pending && <Badge tone="neutral" className="ml-2">waiting for sign-in</Badge>}
                      </td>
                      <td>
                        <Select
                          aria-label={`Role of ${grantName(grant)}`}
                          value={grant.role}
                          title={roleHint(grant.role)}
                          onChange={(event) =>
                            setDraft((list) =>
                              list.map((g) => (grantKey(g) === grantKey(grant) ? { ...g, role: event.target.value as KnowledgeGrant["role"] } : g)),
                            )
                          }
                        >
                          {GRANT_ROLES.map((r) => (
                            <option key={r} value={r}>
                              {r}
                            </option>
                          ))}
                        </Select>
                      </td>
                      <td className="text-right">
                        <button
                          type="button"
                          className="text-xs underline"
                          aria-label={`Remove ${grantName(grant)}`}
                          onClick={() => setDraft((list) => list.filter((g) => grantKey(g) !== grantKey(grant)))}
                        >
                          Remove
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <div className="flex flex-wrap items-end gap-2">
              <Select aria-label="Grant to" value={kind} onChange={(event) => { setKind(event.target.value as Kind); setSubject(""); }} className="w-36">
                <option value="user">a member</option>
                <option value="email">an e-mail</option>
                <option value="group">a group</option>
                <option value="project">a project</option>
              </Select>
              {kind === "user" ? (
                <Select aria-label="Member" value={subject} onChange={(event) => setSubject(event.target.value)} className="w-56">
                  <option value="">Choose a member</option>
                  {memberRows.map((m) => (
                    <option key={m.user_id} value={m.user_id}>
                      {m.email ?? m.user_id}
                    </option>
                  ))}
                </Select>
              ) : kind === "project" ? (
                <Select aria-label="Project" value={subject} onChange={(event) => setSubject(event.target.value)} className="w-56">
                  <option value="">Choose a project</option>
                  {(projects.data?.items ?? []).map((p) => (
                    <option key={p.slug} value={p.slug}>
                      {p.name ?? p.slug}
                    </option>
                  ))}
                </Select>
              ) : (
                <Input
                  aria-label={kind === "email" ? "E-mail" : "Group"}
                  value={subject}
                  placeholder={kind === "email" ? "ana@example.com" : "IdP group"}
                  onChange={(event) => setSubject(event.target.value)}
                  className="w-56"
                />
              )}
              <Select aria-label="Role" value={role} onChange={(event) => setRole(event.target.value as KnowledgeGrant["role"])} className="w-28">
                {GRANT_ROLES.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </Select>
              <button type="button" className={buttonClasses("secondary", "sm")} onClick={add} disabled={!subject.trim()}>
                Add
              </button>
              <button type="button" className={buttonClasses("primary", "sm")} onClick={() => void save()} disabled={!dirty}>
                Save access
              </button>
            </div>
            <p className="text-xs text-ink-muted">
              viewer reads · editor feeds, syncs and distils · owner also shares and archives. A project grant covers that project&apos;s
              own members, not everyone with an organisation role. An e-mail with no member yet waits for that person.
            </p>
          </div>
        ) : (
          <p className="text-ink-muted">Who holds a role is shown to the owners of this base.</p>
        )}
        {notice && <p role="status">{notice}</p>}
        {error && <ErrorNote>{error}</ErrorNote>}
      </div>
    </Card>
  );
}
