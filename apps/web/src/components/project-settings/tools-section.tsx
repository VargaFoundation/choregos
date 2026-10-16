// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { SettingsSection } from "@/components/project-settings/section";
import { Button, Card, Empty, ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";
import { eur } from "@/lib/format";
import { useProjectConfigPatch } from "@/lib/project-config";

/** The tools an agent of this project may call. */
export function ToolsSection({ slug }: { slug: string }) {
  const tools = useQuery({ queryKey: ["project-tools", slug], queryFn: () => api.projectTools(slug) });
  const client = useQueryClient();
  const { patch, pending, message, error } = useProjectConfigPatch(slug);
  const [edit, setEdit] = useState<{ all: boolean; names: string[] } | null>(null);
  const current = {
    all: tools.data?.allows_all ?? false,
    names: (tools.data?.tools ?? []).filter((t) => t.allowed).map((t) => t.name),
  };
  const choice = edit ?? current;
  const dirty = edit !== null && (edit.all !== current.all || JSON.stringify([...edit.names].sort()) !== JSON.stringify([...current.names].sort()));
  async function save() {
    // `*` is explicit: it opens whatever the catalogue opens to this project, now and later.
    const saved = await patch({ tools: choice.all ? ["*"] : choice.names }, { saved: "Tools saved" });
    if (saved) {
      setEdit(null);
      await client.invalidateQueries({ queryKey: ["project-tools", slug] });
    }
  }
  return (
    <SettingsSection id="tools" title="Tools" lead="The catalogue of tools the deployment offers, and which this project may use.">
      <Card title="Tool catalogue">
        {(tools.data?.tools ?? []).length === 0 ? (
          <Empty>No tool declared by the deployment.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table>
              <thead>
                <tr>
                  <th>Tool</th>
                  <th>Provider</th>
                  <th>Open to</th>
                  <th>Per call</th>
                  <th>This project</th>
                </tr>
              </thead>
              <tbody>
                {(tools.data?.tools ?? []).map((tool) => (
                  <tr key={tool.name}>
                    <td>
                      <span className="font-mono text-xs">{tool.name}</span>
                      <p className="text-xs text-ink-muted">{tool.description}</p>
                    </td>
                    <td className="text-xs">
                      {tool.provider}
                      {tool.source === "mcp" ? " · remote MCP" : ""}
                      {tool.needs_credential ? " · platform key" : " · no key"}
                    </td>
                    <td className="text-xs">{(tool.groups ?? []).join(", ") || "Everyone"}</td>
                    <td className="text-xs">{eur(tool.price_eur ?? 0)}</td>
                    <td className="text-xs">
                      <label className="flex items-center gap-2">
                        <input
                          type="checkbox"
                          aria-label={`Allow ${tool.name}`}
                          checked={choice.all || choice.names.includes(tool.name)}
                          disabled={choice.all}
                          onChange={(e) =>
                            setEdit({
                              all: false,
                              names: e.target.checked ? [...choice.names, tool.name] : choice.names.filter((n) => n !== tool.name),
                            })
                          }
                        />
                        {choice.all ? "Everything" : choice.names.includes(tool.name) ? "Allowed" : "No"}
                      </label>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {(tools.data?.tools ?? []).length > 0 && (
          <div className="mt-3 space-y-2">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={choice.all}
                onChange={(e) => setEdit({ all: e.target.checked, names: choice.names })}
              />
              Open the whole catalogue to this project (<span className="font-mono">*</span>): everything the catalogue opens to it, now and later
            </label>
            <div className="flex flex-wrap items-center justify-end gap-3">
              {message && (
                <p className="text-sm text-ok" role="status">
                  {message}
                </p>
              )}
              {error && <ErrorNote>{error}</ErrorNote>}
              <Button tone="primary" disabled={!dirty || pending} onClick={() => void save()}>
                Save tools
              </Button>
            </div>
          </div>
        )}
        <p className="mt-2 text-xs text-ink-muted">
          The groups a tool is open to are read-only here: they are set in the project&apos;s <code>groups</code> and only a holder of{" "}
          <code>tools:grant</code> can change them.
        </p>
      </Card>
    </SettingsSection>
  );
}
