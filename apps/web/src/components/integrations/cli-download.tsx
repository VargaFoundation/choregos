// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useState, useSyncExternalStore } from "react";
import { Alert, Button, Card, Code, ErrorNote, Select } from "@/components/ui";
import { api } from "@/lib/api";
import {
  type BrowserPlatform,
  type CliOs,
  detectPlatform,
  firstCommands,
  formatSize,
  installCommand,
  OS_LABELS,
  recommendedFile,
  releaseUrl,
} from "@/lib/cli-download";

type UserAgentData = {
  platform?: string;
  getHighEntropyValues?: (hints: string[]) => Promise<{ architecture?: string }>;
};

function userAgentData(): UserAgentData | undefined {
  return (navigator as Navigator & { userAgentData?: UserAgentData }).userAgentData;
}

const NEVER_CHANGES = () => () => undefined;

/** What the browser says about its machine; nothing on the server, which has no browser. */
function useBrowserPlatform(): BrowserPlatform {
  const userAgent = useSyncExternalStore(NEVER_CHANGES, () => navigator.userAgent, () => "");
  const platform = useSyncExternalStore(NEVER_CHANGES, () => userAgentData()?.platform ?? "", () => "");
  const [architecture, setArchitecture] = useState<string | undefined>(undefined);
  useEffect(() => {
    void userAgentData()
      ?.getHighEntropyValues?.(["architecture"])
      .then((values) => setArchitecture(values.architecture))
      .catch(() => undefined);
  }, []);
  return detectPlatform(userAgent, { platform: platform || undefined, architecture });
}

/**
 * Integrations › CLI: the `choregos` CLI from THIS instance (S62-08), in the version it runs, through
 * the console's own host. The one-line install for the browser's system first, every file with its
 * checksum after, then the first commands. A build without binaries says so and links the release.
 */
export function CliDownload({ origin, version, org }: { origin: string; version?: string; org: string }) {
  const manifest = useQuery({ queryKey: ["cli-manifest"], queryFn: () => api.cliManifest(), staleTime: 60_000 });
  const platform = useBrowserPlatform();
  const [chosen, setChosen] = useState<CliOs | null>(null);
  const os: CliOs = chosen ?? platform.os ?? "linux";

  if (manifest.isPending) return <Card title="Install the CLI">Reading what this instance bundles…</Card>;
  if (manifest.isError) {
    return (
      <Card title="Install the CLI">
        <ErrorNote>{manifest.error.message}</ErrorNote>
      </Card>
    );
  }
  const bundle = manifest.data;
  if (!bundle) {
    return (
      <Card title="Install the CLI">
        <div data-testid="cli-not-bundled">
        <Alert tone="warn" title="This build bundles no CLI">
          This instance was built without the <Code>choregos</Code> binaries.{" "}
          {version ? (
            <>
              Download version {version}, the one it runs, from its{" "}
              <a href={releaseUrl(version)} target="_blank" rel="noreferrer" data-testid="cli-release">
                GitHub release
              </a>
              .
            </>
          ) : (
            <>Download the version it runs from the project&apos;s GitHub releases.</>
          )}
        </Alert>
        </div>
      </Card>
    );
  }

  const recommended = recommendedFile(bundle.files, { os, arch: chosen ? null : platform.arch });
  return (
    <Card title="Install the CLI">
      <div className="space-y-4 text-sm">
        <p>
          <Code data-testid="cli-version">choregos {bundle.version}</Code>, the version this instance runs, served by
          the instance itself: no Python, no access to GitHub needed.
        </p>
        <div className="space-y-2">
          <label className="flex items-center gap-2">
            <span className="text-ink-muted">System</span>
            <Select
              aria-label="System"
              value={os}
              onChange={(event) => setChosen(event.target.value as CliOs)}
              className="w-auto"
            >
              {(Object.keys(OS_LABELS) as CliOs[]).map((value) => (
                <option key={value} value={value}>
                  {OS_LABELS[value]}
                  {value === platform.os ? " (this computer)" : ""}
                </option>
              ))}
            </Select>
          </label>
          <p className="text-xs text-ink-muted">
            {os === "windows"
              ? "In PowerShell: installs into %LOCALAPPDATA%\\Programs\\choregos and adds it to your PATH."
              : "In a terminal: installs into ~/.local/bin, no sudo; the script checks the sha256 before installing."}
          </p>
          <CopyBlock code={installCommand(os, origin)} testId="cli-install-command" />
        </div>

        <div>
          <p className="mb-1">Or download a file (each one checked against the manifest):</p>
          <div className="overflow-x-auto">
            <table aria-label="CLI files">
              <thead>
                <tr>
                  <th>File</th>
                  <th>System</th>
                  <th>Size</th>
                  <th>sha256</th>
                </tr>
              </thead>
              <tbody>
                {bundle.files.map((file) => (
                  <tr key={file.name} data-recommended={file.name === recommended?.name ? "true" : undefined}>
                    <td>
                      <a href={`/downloads/cli/${encodeURIComponent(file.name)}`} download={file.name}>
                        {file.name}
                      </a>
                      {file.name === recommended?.name && <span className="ml-2 text-xs">(for this system)</span>}
                    </td>
                    <td className="text-xs">
                      {OS_LABELS[file.os]} {file.arch}
                    </td>
                    <td className="text-xs">{formatSize(file.size)}</td>
                    <td className="break-all font-mono text-xs">{file.sha256}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="space-y-2">
          <p>
            Then sign in with a full-access token (My account › Access tokens), and keep it in step later with{" "}
            <Code>choregos self update</Code>:
          </p>
          <CopyBlock code={firstCommands(os, origin, org)} testId="cli-first-commands" />
        </div>
      </div>
    </Card>
  );
}

/** A block to copy. Without `navigator.clipboard` (a page over HTTP), the text stays selectable. */
function CopyBlock({ code, testId }: { code: string; testId: string }) {
  const [copied, setCopied] = useState(false);
  const clipboard = typeof navigator !== "undefined" ? navigator.clipboard : undefined;
  return (
    <div className="relative">
      <pre className="whitespace-pre-wrap break-all bg-surface-sunken p-3 text-xs" data-testid={testId}>
        <code>{code}</code>
      </pre>
      {clipboard && (
        <div className="absolute right-2 top-2">
          <Button size="sm" onClick={() => void clipboard.writeText(code).then(() => setCopied(true))}>
            {copied ? "Copied" : "Copy"}
          </Button>
        </div>
      )}
    </div>
  );
}
