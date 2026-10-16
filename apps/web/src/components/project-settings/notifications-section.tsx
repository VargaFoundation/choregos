// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { SettingsSection } from "@/components/project-settings/section";
import { Badge, Button, Card, ErrorNote, Field, Input } from "@/components/ui";
import { api } from "@/lib/api";
import { connectorState } from "@/lib/connector-state";
import { useProjectConfigPatch, type ProjectConfigValue } from "@/lib/project-config";
import type { ProjectDto } from "@/lib/types";

/** Where the project announces itself: the notify connector (Connections), a Slack channel, e-mail addresses. */
export function NotificationsSection({ slug }: { slug: string }) {
  const project = useQuery({ queryKey: ["project", slug], queryFn: () => api.project(slug) });
  const connectors = useQuery({ queryKey: ["connectors", slug], queryFn: () => api.connectors(slug) });
  const notify = (connectors.data ?? []).find((c) => c.kind === "notify");
  const state = notify ? connectorState({ connector: notify, required: false }) : null;
  return (
    <SettingsSection
      id="notifications"
      title="Notifications"
      lead="Where release trains, findings and memory reviews announce themselves, and ask for a decision."
    >
      <Card title="Notify connector">
        <p className="flex flex-wrap items-center gap-2 text-sm">
          {notify ? (
            <>
              <span>{notify.type}</span>
              {state && <Badge tone={state.tone}>{state.label}</Badge>}
            </>
          ) : (
            <span className="text-ink-muted">No notify connector is configured.</span>
          )}
          <Link href="#connections" className="text-xs">
            {notify ? "Edit it in Connections" : "Configure it in Connections"}
          </Link>
        </p>
      </Card>
      {project.data && (
        <NotifyForm key={JSON.stringify((project.data.config as ProjectConfigValue).notify ?? {})} slug={slug} project={project.data} />
      )}
    </SettingsSection>
  );
}

/** `a@x.dev, b@x.dev` into addresses, dropping blanks. */
export function parseEmails(text: string): string[] {
  return text
    .split(/[,\n;]/)
    .map((part) => part.trim())
    .filter(Boolean);
}

function NotifyForm({ slug, project }: { slug: string; project: ProjectDto }) {
  const notify = (project.config as ProjectConfigValue).notify ?? {};
  const { patch, pending, message, error } = useProjectConfigPatch(slug);
  const [channel, setChannel] = useState(notify.slack_channel ?? "");
  const [emails, setEmails] = useState((notify.emails ?? []).join(", "));
  const invalid = parseEmails(emails).filter((e) => !/^[^@\s]+@[^@\s]+$/.test(e));
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        const list = parseEmails(emails);
        void patch(
          (latest) => ({
            notify: {
              ...latest.notify,
              slack_channel: channel.trim() || undefined,
              emails: list.length > 0 ? list : undefined,
            },
          }),
          { saved: "Notification settings saved" },
        );
      }}
    >
      <Card title="Channels">
        <div className="grid gap-3 md:grid-cols-2">
          <Field label="Slack channel" hint="For example #choregos-dev. Needs a notify connector that is connected.">
            {(p) => <Input {...p} value={channel} placeholder="#choregos-dev" onChange={(e) => setChannel(e.target.value)} />}
          </Field>
          <Field
            label="E-mail addresses"
            hint="Comma separated."
            error={invalid.length > 0 ? `Not an address: ${invalid.join(", ")}` : undefined}
          >
            {(p) => <Input {...p} value={emails} placeholder="po@acme.dev, lead@acme.dev" onChange={(e) => setEmails(e.target.value)} />}
          </Field>
        </div>
        <div className="mt-3 flex flex-wrap items-center justify-end gap-3">
          {message && (
            <p className="text-sm text-ok" role="status">
              {message}
            </p>
          )}
          {error && <ErrorNote>{error}</ErrorNote>}
          <Button tone="primary" type="submit" disabled={pending || invalid.length > 0}>
            Save notification settings
          </Button>
        </div>
      </Card>
    </form>
  );
}
