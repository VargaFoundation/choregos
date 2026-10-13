// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { courte } from "@/components/passages";
import { MarkdownView } from "@/components/markdown-view";
import { Badge, Empty, ErrorNote, Select } from "@/components/ui";
import { api } from "@/lib/api";
import { type ArtifactGroup, filenameOf, groupArtifacts, humanSize, readersOf, viewerOf } from "@/lib/artifacts";
import { shortDate } from "@/lib/format";
import { sarifRows } from "@/lib/sarif";
import type { Artifact, HandOff } from "@/lib/types";

/**
 * The work item's artifacts (S31-04, ADR 0049): one tab per named output, its revisions, who
 * produced each one and who read it (the hand-off receipts, S25-04), a viewer for what the console
 * can show safely, and a download for everything. The bytes come from the API, which checked their
 * digest before serving them; a refused read says so instead of showing anything.
 */
export function Artifacts({
  itemId,
  slug,
  artifacts,
  handOffs,
}: {
  itemId: string;
  slug: string;
  artifacts: Artifact[];
  handOffs: HandOff[];
}) {
  const groups = useMemo(() => groupArtifacts(artifacts), [artifacts]);
  const [selected, setSelected] = useState<string | null>(null);
  const current = groups.find((g) => g.name === selected) ?? groups[0];
  if (!current) return <Empty>no artifact yet: the steps have not produced any output.</Empty>;
  return (
    <div className="space-y-3" data-testid="artifacts">
      <div role="tablist" aria-label="artifacts" className="flex flex-wrap gap-1 border-b border-line">
        {groups.map((group) => (
          <button
            key={group.name}
            type="button"
            role="tab"
            id={`artifact-tab-${group.name}`}
            aria-selected={group.name === current.name}
            aria-controls={`artifact-panel-${group.name}`}
            data-artifact={group.name}
            onClick={() => setSelected(group.name)}
            className={
              "min-h-11 px-3 font-mono text-xs " +
              (group.name === current.name ? "border-b-2 border-accent text-ink" : "text-ink-muted hover:text-ink")
            }
          >
            {group.name}
          </button>
        ))}
      </div>
      <ArtifactPanel key={current.name} group={current} itemId={itemId} slug={slug} handOffs={handOffs} />
    </div>
  );
}

function ArtifactPanel({
  group,
  itemId,
  slug,
  handOffs,
}: {
  group: ArtifactGroup;
  itemId: string;
  slug: string;
  handOffs: HandOff[];
}) {
  const [version, setVersion] = useState(group.latest.version);
  const artifact = group.revisions.find((r) => r.version === version) ?? group.latest;
  const ref = `${artifact.name}@${artifact.version}`;
  const content = useQuery({
    queryKey: ["artifact-content", itemId, ref],
    queryFn: () => api.artifactContent(itemId, ref),
    retry: false,
  });
  const url = useObjectUrl(content.data);
  const readers = readersOf(artifact, handOffs);
  return (
    <section
      role="tabpanel"
      id={`artifact-panel-${group.name}`}
      aria-labelledby={`artifact-tab-${group.name}`}
      className="space-y-3"
      data-version={artifact.version}
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-muted">
        <Badge tone="neutral">{artifact.type}</Badge>
        <span>{artifact.media_type}</span>
        <span>{humanSize(artifact.size)}</span>
        <code className="font-mono" title={artifact.digest} data-testid="artifact-digest">
          {courte(artifact.digest)}
        </code>
        {group.revisions.length > 1 ? (
          <label className="flex items-center gap-1">
            revision
            <Select
              aria-label="revision"
              value={artifact.version}
              onChange={(event) => setVersion(Number(event.target.value))}
              className="w-20"
            >
              {group.revisions.map((r) => (
                <option key={r.version} value={r.version}>
                  v{r.version}
                </option>
              ))}
            </Select>
          </label>
        ) : (
          <span>v{artifact.version}</span>
        )}
        {url && (
          <a href={url} download={filenameOf(artifact)} className="text-accent-strong" data-testid="artifact-download">
            download {filenameOf(artifact)}
          </a>
        )}
      </div>
      <p className="text-xs text-ink-muted" data-testid="artifact-producer">
        produced by{" "}
        <Link href={`/p/${slug}/runs/${artifact.produced_by.run_id}`}>
          {artifact.produced_by.stage ?? "a step"}
          {artifact.produced_by.attempt ? ` · attempt ${artifact.produced_by.attempt}` : ""}
        </Link>
        {artifact.produced_by.agent ? ` · agent ${artifact.produced_by.agent}@${artifact.produced_by.agent_version}` : ""}
        {artifact.created_at ? ` · ${shortDate(artifact.created_at)}` : ""}
        {readers.length > 0 && (
          <>
            {" "}
            · read by{" "}
            {readers.map((reader, index) => (
              <span key={reader.run_id}>
                {index > 0 ? ", " : ""}
                <Link href={`/p/${slug}/runs/${reader.run_id}`} data-testid="artifact-reader">
                  {reader.stage ?? reader.run_id}
                </Link>
              </span>
            ))}
          </>
        )}
      </p>
      {content.error ? (
        <ErrorNote>
          <span data-testid="artifact-refused">this revision is not shown: {(content.error as Error).message}</span>
        </ErrorNote>
      ) : !content.data ? (
        <Empty>loading the content…</Empty>
      ) : (
        <Viewer artifact={artifact} blob={content.data} url={url} />
      )}
    </section>
  );
}

/** An object URL for a blob, revoked when the blob changes or the panel goes. */
function useObjectUrl(blob: Blob | undefined): string | null {
  const url = useMemo(() => (blob ? URL.createObjectURL(blob) : null), [blob]);
  useEffect(
    () => () => {
      if (url) URL.revokeObjectURL(url);
    },
    [url],
  );
  return url;
}

function useText(blob: Blob): string | null {
  const [text, setText] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    void blob.text().then((value) => alive && setText(value));
    return () => {
      alive = false;
    };
  }, [blob]);
  return text;
}

/** Office files are downloaded, never rendered in the browser (S31-06). */
const OFFICE = new Set(["docx", "xlsx", "pptx"]);

function Viewer({ artifact, blob, url }: { artifact: Artifact; blob: Blob; url: string | null }) {
  const kind = viewerOf(artifact);
  if (kind === "image") {
    return url ? (
      // eslint-disable-next-line @next/next/no-img-element -- an object URL, not an optimisable asset
      <img src={url} alt={artifact.name} className="max-h-[32rem] max-w-full border border-line" data-testid="artifact-image" />
    ) : null;
  }
  if (kind === "pdf") {
    // The browser's own PDF viewer, on bytes typed `application/pdf`: never rendered as a page.
    return url ? (
      <iframe src={url} title={`${artifact.name} (PDF)`} className="h-[36rem] w-full border border-line" data-testid="artifact-pdf" />
    ) : null;
  }
  if (kind === "video") {
    return url ? (
      <video controls preload="metadata" className="max-h-[32rem] max-w-full border border-line" data-testid="artifact-video">
        <source src={url} type={artifact.media_type} />
        <track kind="captions" />
        Your browser cannot play {artifact.media_type}: download the file.
      </video>
    ) : null;
  }
  if (kind === "download") {
    const office = OFFICE.has(artifact.type);
    return (
      <Empty title={filenameOf(artifact)}>
        <span data-testid="artifact-download-only">
          {office
            ? "An office file is downloaded and opened in its application; the console does not render it."
            : "The console does not open this type; download it to read it."}
        </span>
      </Empty>
    );
  }
  return <TextViewer kind={kind} blob={blob} />;
}

function TextViewer({ kind, blob }: { kind: "markdown" | "sarif" | "json" | "text"; blob: Blob }) {
  const text = useText(blob);
  if (text === null) return <Empty>reading…</Empty>;
  if (kind === "markdown") return <MarkdownView text={text} />;
  if (kind === "sarif") return <SarifTable text={text} />;
  let shown = text;
  if (kind === "json") {
    try {
      shown = JSON.stringify(JSON.parse(text), null, 2);
    } catch {
      shown = text;
    }
  }
  return (
    <pre className="max-h-[32rem] overflow-auto border border-line p-2 font-mono text-xs" data-testid="artifact-text">
      {shown}
    </pre>
  );
}

function SarifTable({ text }: { text: string }) {
  let rows;
  try {
    rows = sarifRows(text);
  } catch {
    return <ErrorNote>this SARIF report is not valid JSON.</ErrorNote>;
  }
  if (rows.length === 0) return <Empty>the report has no result.</Empty>;
  return (
    <div className="overflow-x-auto">
      <table aria-label="sarif results">
        <thead>
          <tr>
            <th>level</th>
            <th>rule</th>
            <th>message</th>
            <th>location</th>
            <th>tool</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index} data-level={row.level}>
              <td>{row.level}</td>
              <td className="font-mono text-xs">{row.rule}</td>
              <td className="text-sm">{row.message}</td>
              <td className="font-mono text-xs">{row.location}</td>
              <td className="text-xs text-ink-muted">{row.tool}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
