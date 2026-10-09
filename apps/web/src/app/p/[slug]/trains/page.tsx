// SPDX-License-Identifier: Apache-2.0
"use client";

import { Heading } from "@varga/design-system";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { use, useState } from "react";
import { Button, Card, Empty, ErrorNote, EtatDeLecture, StateBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { relative, shortDate } from "@/lib/format";
import { GesteConfirme } from "@/components/geste-confirme";

const ENVS = ["prod", "staging"];

export default function TrainsPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  return (
    <div className="space-y-4">
      <Heading as="h2" size="md">
        release trains
      </Heading>
      {ENVS.map((env) => (
        <TrainCard key={env} slug={slug} env={env} />
      ))}
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
            pending batch: <strong>{data.batch_size}</strong> ticket(s)
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
          <div className="flex flex-wrap items-center gap-2">
            <Button tone="primary" onClick={() => act("depart")} disabled={data.frozen || data.batch_size === 0}>
              depart now
            </Button>
            {data.frozen ? (
              <Button onClick={() => act("unfreeze")}>unfreeze</Button>
            ) : (
              <>
                <input
                  aria-label="freeze reason"
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  placeholder="freeze reason (required)"
                  className="min-w-56 rounded border border-line bg-surface px-2 py-1.5 text-sm"
                />
                <Button tone="danger" onClick={() => act("freeze")}>
                  freeze
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
    <Card title="batch history">
      <div className="overflow-x-auto">
        <table>
          <thead>
            <tr>
              <th>batch</th>
              <th>env</th>
              <th>status</th>
              <th>tickets</th>
              <th>when</th>
              <th>verdict</th>
              <th>
                <span className="sr-only">actions</span>
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
                        question={`approve the production release of batch R-${release.batch_no}? its ${release.items?.length ?? 0} item(s) go to ${release.env}.`}
                        confirmer={`approve R-${release.batch_no}`}
                        action={async () => {
                          await api.approveRelease(release.id);
                          await queryClient.invalidateQueries({ queryKey: ["releases", slug] });
                        }}
                      >
                        approve
                      </GesteConfirme>
                      <GesteConfirme
                        tonDuBouton="danger"
                        question={`abort batch R-${release.batch_no}? nothing of it reaches ${release.env}.`}
                        confirmer={`abort R-${release.batch_no}`}
                        raison={{ label: "why", requise: true }}
                        action={async (raison) => {
                          await api.abortRelease(release.id, raison);
                          await queryClient.invalidateQueries({ queryKey: ["releases", slug] });
                        }}
                      >
                        abort
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
      {releases.data?.items.length === 0 && <Empty>no batch</Empty>}
    </Card>
  );
}
