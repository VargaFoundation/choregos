// SPDX-License-Identifier: Apache-2.0
"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { Alert, Badge, Button, Card, Code, Field, Input, ReadState } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import type { GitHubAppRegistrationStatus } from "@/lib/types";

const DOCS = "https://github.com/VargaFoundation/choregos/blob/main/docs/deployment.md#the-github-app";

/**
 * Step 1 of GitHub's App Manifest flow: a real form POSTed to github.com, whose one field, `manifest`,
 * is the JSON the platform built. GitHub only accepts the manifest that way (not a link, not a fetch).
 */
export function postManifest(actionUrl: string, manifest: Record<string, unknown>, doc: Document = document): HTMLFormElement {
  const form = doc.createElement("form");
  form.method = "post";
  form.action = actionUrl;
  form.style.display = "none";
  const field = doc.createElement("input");
  field.type = "hidden";
  field.name = "manifest";
  field.value = JSON.stringify(manifest);
  form.appendChild(field);
  doc.body.appendChild(form);
  form.submit();
  return form;
}

/**
 * The instance's GitHub App (S70-21, ADR 0161): the one the deployment's settings name, or the one a
 * platform administrator creates here from a manifest, sealed in the database. Public or private is a
 * choice made here, never a default.
 */
export function GitHubAppRegistration({ outcome }: { outcome?: string | null }) {
  const status = useQuery({ queryKey: ["platform-github-app"], queryFn: () => api.platformGitHubApp(), retry: false });
  if (status.error instanceof ApiError && status.error.status === 403) {
    return <Alert tone="neutral">Only a platform administrator manages the instance&apos;s GitHub App.</Alert>;
  }
  return (
    <div className="space-y-4">
      {outcome === "created" && (
        <Alert tone="succeeded" title="The GitHub App is created">
          Its credentials are sealed on this instance. Install it on the repositories from a project&apos;s connectors.
        </Alert>
      )}
      <ReadState lecture={status} quoi="the GitHub App" />
      {status.data && <Current status={status.data} />}
      {status.data && (status.data.blocked_by ? <Blocked reason={status.data.blocked_by} /> : <CreateForm status={status.data} />)}
      {status.data && <WhatItAsks status={status.data} />}
    </div>
  );
}

function Current({ status }: { status: GitHubAppRegistrationStatus }) {
  const client = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const forget = useMutation({
    mutationFn: () => api.forgetPlatformGitHubApp(),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["platform-github-app"] }),
  });
  if (status.source === "none") {
    return (
      <Card title="No GitHub App yet">
        <p className="text-sm text-ink-muted">
          The GitHub connectors, the repository picker and the run&apos;s git token need one. Create it below: GitHub
          registers it under your account or an organisation&apos;s, and its owner is whoever registers it.
        </p>
      </Card>
    );
  }
  if (status.source === "deployment") {
    return (
      <Card title="The deployment's GitHub App">
        <p className="text-sm">
          App <span className="font-mono" data-verbatim>{status.app_id}</span>, named by the deployment&apos;s settings (
          <Code>CHOREGOS_GITHUB_APP_ID</Code> and its private key). They win over an App created here.
        </p>
      </Card>
    );
  }
  return (
    <Card title="The GitHub App created from this console" action={status.public != null && <Badge tone="neutral">{status.public ? "public" : "private"}</Badge>}>
      <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
        <dt className="text-ink-muted">Name</dt>
        <dd data-verbatim>{status.name}</dd>
        <dt className="text-ink-muted">App id</dt>
        <dd className="font-mono" data-verbatim>{status.app_id}</dd>
        <dt className="text-ink-muted">Owner</dt>
        <dd data-verbatim>{status.owner_login}</dd>
        <dt className="text-ink-muted">Created by</dt>
        <dd data-verbatim>{status.created_by}</dd>
      </dl>
      {!status.usable && (
        <Alert tone="danger" title="Its credentials do not open" className="mt-3">
          The key that sealed them is no longer in <Code>CHOREGOS_SECRETS_KEKS</Code>: put it back, or forget this App and
          create another.
        </Alert>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {status.html_url && (
          <a className="text-sm underline" href={status.html_url} target="_blank" rel="noreferrer">
            Open it on GitHub
          </a>
        )}
        {confirming ? (
          <>
            <span className="text-sm">Its credentials are deleted here; the App stays on GitHub until its owner deletes it.</span>
            <Button tone="danger" size="sm" onClick={() => forget.mutate()} disabled={forget.isPending}>
              Forget it
            </Button>
            <Button size="sm" onClick={() => setConfirming(false)}>
              Keep it
            </Button>
          </>
        ) : (
          <Button size="sm" onClick={() => setConfirming(true)}>
            Forget this App…
          </Button>
        )}
      </div>
      {forget.error && <Alert tone="danger" className="mt-2">{forget.error.message}</Alert>}
    </Card>
  );
}

function Blocked({ reason }: { reason: string }) {
  return (
    <Alert tone="neutral" title="A GitHub App cannot be created here now">
      {reason}
    </Alert>
  );
}

function CreateForm({ status }: { status: GitHubAppRegistrationStatus }) {
  const [name, setName] = useState(status.suggested_name);
  const [organization, setOrganization] = useState("");
  const [visibility, setVisibility] = useState<"public" | "private" | null>(null);
  const [reachable, setReachable] = useState(true);
  const groupId = useId();
  const start = useMutation({
    mutationFn: () =>
      api.startGitHubAppManifest({
        name: name.trim(),
        organization: organization.trim() || null,
        public: visibility === "public",
        webhook_active: reachable,
      }),
    onSuccess: (form) => postManifest(form.action_url, form.manifest),
  });
  return (
    <Card title="Create the GitHub App">
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault();
          if (visibility !== null) start.mutate();
        }}
      >
        <Field label="Name on GitHub" hint="Unique across GitHub, 34 characters at most.">
          {(props) => <Input {...props} value={name} maxLength={34} required onChange={(e) => setName(e.target.value)} />}
        </Field>
        <Field
          label="Owner organisation on GitHub (optional)"
          hint="Empty: your personal account. You must be an owner of the organisation to register an App for it."
        >
          {(props) => <Input {...props} value={organization} onChange={(e) => setOrganization(e.target.value)} />}
        </Field>
        <fieldset aria-labelledby={`${groupId}-legend`} className="space-y-1.5">
          <legend id={`${groupId}-legend`} className="text-xs font-medium text-ink-muted">
            Who may install it
          </legend>
          <label className="flex items-start gap-2 text-sm">
            <input type="radio" name={`${groupId}-visibility`} checked={visibility === "private"} onChange={() => setVisibility("private")} />
            <span>
              <strong>Private</strong>: only on the account that owns it. Enough when every repository Choregos works in belongs
              to that account.
            </span>
          </label>
          <label className="flex items-start gap-2 text-sm">
            <input type="radio" name={`${groupId}-visibility`} checked={visibility === "public"} onChange={() => setVisibility("public")} />
            <span>
              <strong>Public</strong>: any GitHub account may install it, as each organisation using this instance does on its own
              account.
            </span>
          </label>
        </fieldset>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={reachable} onChange={(e) => setReachable(e.target.checked)} />
          <span>GitHub can reach this instance: deliver its events to the webhook URL below.</span>
        </label>
        {start.error && <Alert tone="danger">{start.error.message}</Alert>}
        <Button type="submit" tone="accent" disabled={visibility === null || !name.trim() || start.isPending}>
          Continue on GitHub
        </Button>
        <p className="text-xs text-ink-muted">
          GitHub shows the App before registering it, then sends you back here; the credentials it issues are sealed on this
          instance. See <a className="underline" href={DOCS}>the GitHub App</a> in the deployment guide.
        </p>
      </form>
    </Card>
  );
}

function WhatItAsks({ status }: { status: GitHubAppRegistrationStatus }) {
  return (
    <Card title="What the App is asked for">
      <div className="grid gap-4 text-sm md:grid-cols-2">
        <div>
          <p className="mb-1 text-xs font-medium text-ink-muted">Permissions</p>
          <ul className="space-y-0.5 font-mono text-xs">
            {Object.entries(status.permissions).map(([name, level]) => (
              <li key={name}>
                {name}: {level}
              </li>
            ))}
          </ul>
        </div>
        <div>
          <p className="mb-1 text-xs font-medium text-ink-muted">Events</p>
          <ul className="space-y-0.5 font-mono text-xs">
            {status.events.map((event) => (
              <li key={event}>{event}</li>
            ))}
          </ul>
          <p className="mt-1 text-xs text-ink-muted">GitHub delivers the installation events to every App.</p>
        </div>
      </div>
      <dl className="mt-3 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-xs">
        <dt className="text-ink-muted">Webhook URL</dt>
        <dd className="font-mono [overflow-wrap:anywhere]" data-verbatim>{status.webhook_url}</dd>
        <dt className="text-ink-muted">Callback URL</dt>
        <dd className="font-mono [overflow-wrap:anywhere]" data-verbatim>{status.callback_url}</dd>
        <dt className="text-ink-muted">Redirect URL</dt>
        <dd className="font-mono [overflow-wrap:anywhere]" data-verbatim>{status.redirect_url}</dd>
      </dl>
    </Card>
  );
}
