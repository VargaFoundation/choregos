// SPDX-License-Identifier: Apache-2.0
"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { ConfirmedGesture } from "@/components/confirmed-gesture";
import { DocsLink } from "@/components/docs-link";
import { Button, Card, Code, ErrorNote, Field, Input } from "@/components/ui";
import { api } from "@/lib/api";
import type { ApiToken, ApiTokenCreated, MachineAccount } from "@/lib/types";

/** Told when a token is issued, with its secret: the Integrations page puts it in its snippets (S62-11). */
export type OnIssued = (account: MachineAccount, token: ApiTokenCreated) => void;

const SLUG = /^[a-z0-9][a-z0-9-]{0,62}$/;

/**
 * The applications that call the A2A door under their own name (ADR 0131): one account per project,
 * `a2a` tokens that expire, each shown once. An account never decides and survives whoever created it.
 */
export function MachineAccounts({ slug, onIssued }: { slug: string; onIssued?: OnIssued }) {
  const client = useQueryClient();
  const listed = useQuery({
    queryKey: ["machine-accounts", slug],
    queryFn: () => api.machineAccounts(slug),
  });
  const accounts = Array.isArray(listed.data) ? listed.data : [];
  const [name, setName] = useState("");
  const refresh = () =>
    client.invalidateQueries({ queryKey: ["machine-accounts", slug] });
  const create = useMutation({
    mutationFn: () => api.createMachineAccount(slug, { slug: name }),
    onSuccess: () => {
      setName("");
      void refresh();
    },
  });
  const valid = SLUG.test(name);
  return (
    <Card title="Applications" data-testid="machine-accounts">
      <p className="mb-3 text-sm text-ink-muted">
        An application starts this project&apos;s workflows over the A2A door
        under its own name, not a person&apos;s. It never takes a decision, and
        it keeps working when whoever created it leaves.{" "}
        {!onIssued && (
          <Link href={`/p/${slug}/integrations/a2a`} data-testid="machine-accounts-steps">
            Connect one, step by step
          </Link>
        )}{" "}
        <DocsLink slug="integrate/a2a" />
      </p>
      {listed.error ? (
        <ErrorNote>{String(listed.error)}</ErrorNote>
      ) : accounts.length === 0 ? (
        <p
          className="text-sm text-ink-muted"
          data-testid="machine-accounts-none"
        >
          {listed.data ? "No application yet." : "Reading…"}
        </p>
      ) : (
        <ul className="space-y-4 text-sm">
          {accounts.map((account) => (
            <AccountLine
              key={account.slug}
              slug={slug}
              account={account}
              onChange={refresh}
              onIssued={onIssued}
            />
          ))}
        </ul>
      )}
      <div className="mt-4 flex items-end gap-2">
        <Field
          label="New application"
          hint="Lower case letters, digits and hyphens."
          className="min-w-0 flex-1"
        >
          {(props) => (
            <Input
              {...props}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          )}
        </Field>
        <Button
          onClick={() => create.mutate()}
          disabled={!valid || create.isPending}
        >
          Create
        </Button>
      </div>
      {create.error && <ErrorNote>{String(create.error)}</ErrorNote>}
    </Card>
  );
}

function AccountLine({
  slug,
  account,
  onChange,
  onIssued,
}: {
  slug: string;
  account: MachineAccount;
  onChange: () => unknown;
  onIssued?: OnIssued;
}) {
  const [issued, setIssued] = useState<string | null>(null);
  const issue = useMutation({
    mutationFn: () =>
      api.createMachineToken(slug, account.slug, {
        name: `token-${account.tokens.length + 1}`,
      }),
    onSuccess: (token) => {
      setIssued(token.token);
      onIssued?.(account, token);
      void onChange();
    },
  });
  return (
    <li data-testid={`machine-account-${account.slug}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span>
          <strong>{account.display_name}</strong>{" "}
          <span className="text-xs text-ink-muted">
            {account.writes_per_day} writes a day
          </span>
        </span>
        <span className="flex items-center gap-2">
          <Button onClick={() => issue.mutate()} disabled={issue.isPending}>
            Issue a token
          </Button>
          <ConfirmedGesture
            confirmer="Remove the application"
            question={`Remove ${account.display_name}? Its tokens stop working at once, and its name cannot be used again.`}
            action={() => api.removeMachineAccount(slug, account.slug)}
            onDone={() => void onChange()}
          >
            Remove
          </ConfirmedGesture>
        </span>
      </div>
      {issue.error && <ErrorNote>{String(issue.error)}</ErrorNote>}
      {issued && (
        <p className="mt-1 break-all">
          Token, shown once:{" "}
          <Code data-testid={`machine-token-${account.slug}`}>{issued}</Code>
        </p>
      )}
      {account.tokens.length > 0 && (
        <ul className="mt-2 space-y-1 text-xs text-ink-muted">
          {account.tokens.map((token) => (
            <TokenLine
              key={token.id}
              slug={slug}
              account={account.slug}
              token={token}
              onChange={onChange}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

function TokenLine({
  slug,
  account,
  token,
  onChange,
}: {
  slug: string;
  account: string;
  token: ApiToken;
  onChange: () => unknown;
}) {
  return (
    <li className="flex flex-wrap items-center justify-between gap-2">
      <span>
        {token.name} · expires {token.expires_at?.slice(0, 10) ?? "never"} ·
        last used {token.last_used_at?.slice(0, 10) ?? "never"}
      </span>
      <ConfirmedGesture
        confirmer="Revoke the token"
        question={`Revoke ${token.name}? An application still using it is refused at once.`}
        action={() => api.revokeMachineToken(slug, account, token.id)}
        onDone={() => void onChange()}
      >
        Revoke
      </ConfirmedGesture>
    </li>
  );
}
