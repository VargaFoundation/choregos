// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button, ErrorNote, Input } from "@/components/ui";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { GitHubAppStatus } from "@/lib/types";

/** The project slug of the page we are on (`/p/<slug>/...`), or null elsewhere. */
function projectOfLocation(): string | null {
  if (typeof window === "undefined") return null;
  return /^\/p\/([^/]+)/.exec(window.location.pathname)?.[1] ?? null;
}

/** The owner of an `owner/repo` string, lower-cased, or "" when it is not one yet. */
export function ownerOf(repo: string): string {
  const [owner, name] = repo.trim().split("/");
  return owner && name ? owner.toLowerCase() : "";
}

/** Whether the organisation has an installation claimed on this owner (suspended ones do not count). */
export function hasInstallationOn(status: GitHubAppStatus | undefined, owner: string): boolean {
  if (!status || !owner) return true;
  return status.installations.some((i) => i.claimed && !i.suspended && i.account_login.toLowerCase() === owner);
}

/**
 * Sends the person to GitHub to install the Choregos App, and back to this page afterwards. The link
 * carries a signed state made by the API (ADR 0073); it opens in the same tab, since GitHub returns to
 * the Setup URL, which redirects here.
 */
export function InstallOnGitHub({ label = "Install on GitHub" }: { label?: string }) {
  const { org } = useSession();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function go() {
    setBusy(true);
    setError(null);
    try {
      const { pathname, search } = window.location;
      const link = await api.githubInstallLink(org, { project: projectOfLocation(), return_to: `${pathname}${search}#connections` });
      window.location.assign(link.url);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "the install link could not be made");
      setBusy(false);
    }
  }
  return (
    <span className="inline-flex flex-col gap-1">
      <Button size="sm" tone="primary" disabled={busy} onClick={() => void go()}>
        {label}
      </Button>
      {error && <ErrorNote>{error}</ErrorNote>}
    </span>
  );
}

/** What the App can see of this organisation, shared by the picker and the connector card. */
export function useGitHubStatus() {
  const { org } = useSession();
  return useQuery({ queryKey: ["github", org], queryFn: () => api.githubStatus(org), retry: false });
}

/**
 * The repository of a GitHub connector: picked from what the Choregos App can see, never typed together
 * with an installation id (ADR 0073). A repository the App cannot list (a GitHub Enterprise host, a
 * fake) is typed instead; an owner without an installation is warned about, with the way to install.
 */
export function GitHubRepoField({
  id,
  value,
  onChange,
  disabled,
  describedBy,
}: {
  id: string;
  value: string;
  onChange: (value: string | undefined) => void;
  disabled?: boolean;
  describedBy?: string;
}) {
  const { org } = useSession();
  const client = useQueryClient();
  const [typed, setTyped] = useState(false);
  const [q, setQ] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const status = useGitHubStatus();
  const repositories = useQuery({
    queryKey: ["github-repositories", org, q],
    queryFn: () => api.githubRepositories(org, q),
    enabled: status.data?.configured === true,
    retry: false,
  });
  const configured = status.data?.configured !== false;
  const installed = status.data?.installations.some((i) => i.claimed && !i.suspended) ?? false;
  const items = repositories.data?.items ?? [];

  async function refresh() {
    setRefreshing(true);
    try {
      client.setQueryData(["github", org], await api.githubRefresh(org));
      await client.invalidateQueries({ queryKey: ["github-repositories", org] });
    } finally {
      setRefreshing(false);
    }
  }

  if (typed || !configured) {
    const owner = ownerOf(value);
    return (
      <div className="space-y-2">
        <Input
          id={id}
          value={value}
          placeholder="owner/repo"
          disabled={disabled}
          aria-describedby={describedBy}
          onChange={(event) => onChange(event.target.value === "" ? undefined : event.target.value)}
        />
        {!configured && (
          <p className="text-xs text-ink-muted">This deployment has no GitHub App: type the repository as owner/repo.</p>
        )}
        {configured && owner && !hasInstallationOn(status.data, owner) && (
          <div className="flex flex-wrap items-center gap-2 text-xs text-warn" role="status">
            <span>The Choregos App is not installed on {owner} for this organisation.</span>
            <InstallOnGitHub />
          </div>
        )}
        {configured && (
          <button type="button" className="text-xs underline" onClick={() => setTyped(false)}>
            Pick from the list instead
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-2" data-testid="github-repo-field">
      {value && (
        <p className="text-sm">
          Selected: <code className="font-mono">{value}</code>
        </p>
      )}
      {status.isSuccess && !installed ? (
        <div className="space-y-2 rounded-md border border-line bg-surface-sunken p-3 text-xs text-ink-muted">
          <p>The Choregos App is not installed on any GitHub account of this organisation.</p>
          <div className="flex flex-wrap gap-2">
            <InstallOnGitHub />
            <Button size="sm" disabled={refreshing} onClick={() => void refresh()}>
              Refresh
            </Button>
          </div>
        </div>
      ) : (
        <>
          <Input
            id={id}
            type="search"
            value={q}
            placeholder="Search repositories"
            disabled={disabled}
            aria-describedby={describedBy}
            onChange={(event) => setQ(event.target.value)}
          />
          {repositories.error && <ErrorNote>{String(repositories.error)}</ErrorNote>}
          <ul className="max-h-48 divide-y divide-line overflow-auto rounded-md border border-line" aria-label="Matches">
            {items.map((repo) => (
              <li key={`${repo.installation_id}/${repo.full_name}`}>
                <button
                  type="button"
                  disabled={disabled}
                  aria-pressed={repo.full_name === value}
                  className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm hover:bg-surface-sunken aria-pressed:bg-surface-sunken"
                  onClick={() => onChange(repo.full_name)}
                >
                  <span className="font-mono break-all">{repo.full_name}</span>
                  <span className="shrink-0 text-xs text-ink-muted">{repo.private ? "private" : "public"}</span>
                </button>
              </li>
            ))}
            {repositories.isSuccess && items.length === 0 && (
              <li className="px-3 py-2 text-xs text-ink-muted">No repository matches.</li>
            )}
          </ul>
          <div className="flex flex-wrap items-center gap-3 text-xs">
            <button type="button" className="underline" onClick={() => setTyped(true)}>
              Type it instead
            </button>
            <button type="button" className="underline" disabled={refreshing} onClick={() => void refresh()}>
              Refresh
            </button>
            <InstallOnGitHub label="Install on another account" />
          </div>
        </>
      )}
    </div>
  );
}
