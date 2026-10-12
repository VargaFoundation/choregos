// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { use, useState } from "react";
import { Button, Card, Empty, ErrorNote, EtatDeLecture, Heading, Input, StateBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { relative, shortDate } from "@/lib/format";
import type { PolicyDef } from "@/lib/types";
import { GesteConfirme } from "@/components/geste-confirme";

/**
 * The environments that have a release train: the keys of the active policy's `release_train`, in
 * the policy's order (S26-05). The page used to show a fixed `prod` and `staging`, whatever the
 * policy said — a train for an environment the project does not have, and none for one it has.
 */
function trainEnvironments(policy: PolicyDef): string[] {
  return Object.keys(policy.json?.release_train ?? {});
}

export default function TrainsPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const policy = useQuery({ queryKey: ["policy", slug], queryFn: () => api.policy(slug) });
  const envs = policy.data ? trainEnvironments(policy.data) : [];
  return (
    <div className="space-y-4">
      <Heading as="h2" size="md">
        Release trains
      </Heading>
      {!policy.data ? (
        <EtatDeLecture lecture={policy} quoi="the policy's release trains" />
      ) : envs.length === 0 ? (
        <Empty title="no release train">this project&apos;s policy declares no release_train environment.</Empty>
      ) : (
        envs.map((env) => <TrainCard key={env} slug={slug} env={env} />)
      )}
      <History slug={slug} />
    </div>
  );
}

function TrainCard({ slug, env }: { slug: string; env: string }) {
  const queryClient = useQueryClient();
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const train = useQuery({
    queryKey: ["train", slug, env],
    queryFn: () => api.train(slug, env),
    refetchInterval: 15_000,
  });

  async function act(action: "depart" | "freeze" | "unfreeze") {
    setError(null);
    try {
      if (action === "depart") await api.departTrain(slug, env);
      if (action === "freeze") {
        if (!reason.trim()) {
          setError("a reason for the freeze is required");
          return;
        }
        await api.freezeTrain(slug, env, reason);
      }
      if (action === "unfreeze") await api.unfreezeTrain(slug, env);
      queryClient.invalidateQueries({ queryKey: ["train", slug, env] });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "action refused");
    }
  }

  const data = train.data;
  return (
    <Card
      title={`Environment ${env}`}
      action={data && <StateBadge state={data.status} display={data.status} kind={data.frozen ? "blocked" : "work"} />}
    >
      {!data ? (
        <EtatDeLecture lecture={train} quoi={`the ${env} train`} />
      ) : (
        <div className="space-y-3">
          <p className="text-sm">
            Pending batch: <strong>{data.batch_size}</strong> ticket(s)
            {data.next_departure && (
              <span className="text-ink-muted"> · departure {relative(data.next_departure)}</span>
            )}
            {!data.window_open && <span className="text-warn"> · outside the window</span>}
          </p>
          <ul className="font-mono text-xs text-ink-muted">
            {data.pending_items?.map((key) => (
              <li key={key}>{key}</li>
            ))}
          </ul>
          {data.frozen && <ErrorNote>Train frozen: {data.freeze_reason ?? "no reason"}</ErrorNote>}
          <div className="flex flex-wrap items-end gap-2">
            <Button tone="primary" onClick={() => act("depart")} disabled={data.frozen || data.batch_size === 0}>
              Depart now
            </Button>
            {data.frozen ? (
              <Button onClick={() => act("unfreeze")}>Unfreeze</Button>
            ) : (
              <>
                <label className="flex min-w-0 flex-col gap-1">
                  <span className="text-xs text-ink-muted">Freeze reason</span>
                  <Input
                    value={reason}
                    onChange={(event) => setReason(event.target.value)}
                    placeholder="Freeze reason (required)"
                    className="w-auto min-w-56"
                  />
                </label>
                <Button tone="danger" onClick={() => act("freeze")}>
                  Freeze
                </Button>
              </>
            )}
          </div>
          {error && <ErrorNote>{error}</ErrorNote>}
        </div>
      )}
    </Card>
  );
}

function History({ slug }: { slug: string }) {
  const queryClient = useQueryClient();
  const releases = useQuery({ queryKey: ["releases", slug], queryFn: () => api.releases(slug) });
  return (
    <Card title="Batch history">
      <div className="overflow-x-auto">
        <table>
          <thead>
            <tr>
              <th>Batch</th>
              <th>Env</th>
              <th>Status</th>
              <th>Tickets</th>
              <th>When</th>
              <th>Verdict</th>
              <th>
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {(releases.data?.items ?? []).map((release) => (
              <tr key={release.id}>
                <td>R-{release.batch_no}</td>
                <td>{release.env}</td>
                <td>
                  <StateBadge
                    state={release.status}
                    display={release.status}
                    kind={
                      release.status === "done" ? "terminal" : release.status === "rolled_back" ? "blocked" : "work"
                    }
                  />
                </td>
                <td>{release.items?.length ?? 0}</td>
                <td title={shortDate(release.started_at)}>{relative(release.started_at)}</td>
                <td className="text-xs text-ink-muted">
                  {String(release.verdict?.reason ?? (release.verdict?.go ? "go" : ""))}
                </td>
                <td>
                  {release.status === "awaiting_approval" && (
                    <span className="flex flex-wrap gap-2">
                      <GesteConfirme
                        tonDuBouton="primary"
                        ton="primary"
                        question={`Approve the production release of batch R-${release.batch_no}? Its ${release.items?.length ?? 0} item(s) go to ${release.env}.`}
                        confirmer={`Approve R-${release.batch_no}`}
                        action={async () => {
                          await api.approveRelease(release.id);
                          await queryClient.invalidateQueries({ queryKey: ["releases", slug] });
                        }}
                      >
                        Approve
                      </GesteConfirme>
                      <GesteConfirme
                        tonDuBouton="danger"
                        question={`Abort batch R-${release.batch_no}? Nothing of it reaches ${release.env}.`}
                        confirmer={`Abort R-${release.batch_no}`}
                        raison={{ label: "Why", requise: true }}
                        action={async (raison) => {
                          await api.abortRelease(release.id, raison);
                          await queryClient.invalidateQueries({ queryKey: ["releases", slug] });
                        }}
                      >
                        Abort
                      </GesteConfirme>
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <EtatDeLecture lecture={releases} quoi="the batches" />
      {releases.data?.items.length === 0 && <Empty>No batch</Empty>}
    </Card>
  );
}
