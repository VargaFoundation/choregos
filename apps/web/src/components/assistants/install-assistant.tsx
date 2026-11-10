// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { SchemaForm, defaultValues, missingFields, type JsonSchema } from "@/components/schema-form";
import { Button, Card, Empty, ErrorNote, Field, Input, Select } from "@/components/ui";
import { api } from "@/lib/api";
import { assistantsApi } from "@/lib/assistants-api";

/** `a, b` as `["a", "b"]`. */
export function groupsOf(text: string): string[] {
  return text
    .split(",")
    .map((g) => g.trim())
    .filter(Boolean);
}

/**
 * Installs an assistant of an installed plugin (organisation administrators): the configuration is a
 * form drawn from the assistant's `config_schema`, then its audience, its owners and its channel.
 */
export function InstallAssistant({ org, onInstalled }: { org: string; onInstalled?: (slug: string) => void }) {
  const client = useQueryClient();
  const plugins = useQuery({ queryKey: ["plugins", org], queryFn: () => api.plugins(org) });
  const [plugin, setPlugin] = useState("");
  const shipped = useQuery({
    queryKey: ["plugin-assistants", org, plugin],
    queryFn: () => assistantsApi.pluginAssistants(org, plugin),
    enabled: plugin !== "",
  });
  const [name, setName] = useState("");
  const [config, setConfig] = useState<Record<string, unknown>>({});
  const [audience, setAudience] = useState<string | null>(null);
  const [owners, setOwners] = useState("");
  const [channel, setChannel] = useState<"stable" | "candidate">("stable");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const offered = shipped.data?.items ?? [];
  const chosen = offered.find((a) => a.name === name);
  const schema = (chosen?.config_schema ?? null) as JsonSchema | null;
  const lacking = schema ? missingFields(schema, config) : [];

  function choose(next: string) {
    setName(next);
    const found = offered.find((a) => a.name === next);
    setConfig(found?.config_schema ? defaultValues(found.config_schema as JsonSchema) : {});
    setAudience(null);
  }

  async function install() {
    setError(null);
    setBusy(true);
    try {
      const done = await assistantsApi.install(org, {
        plugin,
        assistant: name,
        config,
        owner_groups: groupsOf(owners),
        audience_groups: groupsOf(audience ?? (chosen?.suggested_groups ?? []).join(", ")),
        channel,
      });
      await client.invalidateQueries({ queryKey: ["installations", org] });
      onInstalled?.(done.slug);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Installation refused");
    } finally {
      setBusy(false);
    }
  }

  const items = plugins.data?.items ?? [];
  return (
    <Card title="Install an assistant" eyebrow="Administrators">
      {plugins.data && items.length === 0 ? (
        <Empty>Install a plugin that ships an assistant first (Library, Plugins).</Empty>
      ) : (
        <div className="space-y-4">
          <div className="grid gap-3 md:grid-cols-2">
            <Field label="Plugin">
              {(props) => (
                <Select
                  {...props}
                  value={plugin}
                  onChange={(e) => {
                    setPlugin(e.target.value);
                    setName("");
                    setConfig({});
                  }}
                >
                  <option value="">Choose a plugin</option>
                  {items.map((p) => (
                    <option key={p.name} value={p.name}>
                      {p.name} {p.version}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
            <Field label="Assistant" hint={shipped.data && offered.length === 0 ? "This plugin ships no assistant." : undefined}>
              {(props) => (
                <Select {...props} value={name} onChange={(e) => choose(e.target.value)} disabled={offered.length === 0}>
                  <option value="">Choose an assistant</option>
                  {offered.map((a) => (
                    <option key={a.name} value={a.name}>
                      {a.display_name ?? a.name}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          </div>
          {chosen && (
            <>
              {chosen.description && <p className="text-sm text-ink-muted">{chosen.description}</p>}
              {schema && <SchemaForm schema={schema} value={config} onChange={setConfig} org={org} />}
              <div className="grid gap-3 md:grid-cols-3">
                <Field label="Audience groups" hint="Who may talk to it, comma separated.">
                  {(props) => (
                    <Input
                      {...props}
                      value={audience ?? chosen.suggested_groups.join(", ")}
                      onChange={(e) => setAudience(e.target.value)}
                    />
                  )}
                </Field>
                <Field label="Owner groups" hint="Who configures and binds it.">
                  {(props) => <Input {...props} value={owners} onChange={(e) => setOwners(e.target.value)} />}
                </Field>
                <Field label="Channel">
                  {(props) => (
                    <Select {...props} value={channel} onChange={(e) => setChannel(e.target.value as typeof channel)}>
                      <option value="stable">Stable</option>
                      <option value="candidate">Candidate</option>
                    </Select>
                  )}
                </Field>
              </div>
              {lacking.length > 0 && <p className="text-xs text-ink-muted">Required: {lacking.join(", ")}</p>}
              {error && <ErrorNote>{error}</ErrorNote>}
              <Button tone="primary" disabled={busy || lacking.length > 0} onClick={() => void install()}>
                Install
              </Button>
            </>
          )}
        </div>
      )}
    </Card>
  );
}
