// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { SettingsSection } from "@/components/project-settings/section";
import { Card, Empty } from "@/components/ui";
import { api } from "@/lib/api";
import { eur } from "@/lib/format";

/** The tools an agent of this project may call. */
export function ToolsSection({ slug }: { slug: string }) {
  const tools = useQuery({ queryKey: ["project-tools", slug], queryFn: () => api.projectTools(slug) });
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
                    <td className="text-xs">{tool.allowed ? "Allowed" : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {tools.data?.allows_all && (
          <p className="mt-2 text-xs text-ink-muted">
            This project declares <span className="font-mono">*</span>: the whole catalogue is open to it.
          </p>
        )}
        <p className="mt-2 text-xs text-ink-muted">
          A project&apos;s tool list (<code>tools</code>, <code>groups</code>) is set in its configuration — it is reviewed like code.
        </p>
      </Card>
    </SettingsSection>
  );
}
