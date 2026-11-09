import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import { Artifacts } from "@/components/artifacts";
import { MarkdownView } from "@/components/markdown-view";
import { api, ApiError } from "@/lib/api";
import { filenameOf, firstToOpen, groupArtifacts, readersOf, viewerOf } from "@/lib/artifacts";
import { sarifRows } from "@/lib/sarif";
import type { Artifact, HandOff } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, artifactContent: vi.fn() } };
});

beforeAll(() => {
  // jsdom has no object URLs.
  URL.createObjectURL = vi.fn(() => "blob:artifact");
  URL.revokeObjectURL = vi.fn();
});

const A = `sha256:${"a".repeat(64)}`;
const B = `sha256:${"b".repeat(64)}`;

function artifact(name: string, version: number, extra: Partial<Artifact> = {}): Artifact {
  return {
    name,
    type: "markdown",
    kind: "markdown",
    media_type: "text/markdown",
    version,
    digest: version === 1 ? A : B,
    size: 10,
    produced_by: { run_id: `r${version}`, stage: "refine", attempt: 1, agent: "analyst", agent_version: 3 },
    storage_url: null,
    ...extra,
  };
}

const HAND_OFFS: HandOff[] = [
  {
    output: "spec_markdown",
    current_digest: A,
    events: [
      { kind: "produced", digest: A, run_id: "r1", stage: "refine", attempt: 1, at: "2026-10-12T10:00:00Z" },
      { kind: "read", digest: A, run_id: "r9", stage: "implement", attempt: 1, at: "2026-10-12T11:00:00Z" },
    ],
  },
];

function renderArtifacts(artifacts: Artifact[]) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <Artifacts itemId="w1" slug="billing-api" artifacts={artifacts} handOffs={HAND_OFFS} />
    </QueryClientProvider>,
  );
}

describe("the artifacts of a work item (S31-04)", () => {
  it("groups revisions by name, latest last, and picks a safe viewer", () => {
    const groups = groupArtifacts([artifact("spec_markdown", 2), artifact("spec_markdown", 1), artifact("a", 1)]);
    expect(groups.map((g) => [g.name, g.latest.version, g.revisions.length])).toEqual([
      ["a", 1, 1],
      ["spec_markdown", 2, 2],
    ]);
    expect(viewerOf({ type: "sarif", kind: "report", media_type: "application/sarif+json" })).toBe("sarif");
    expect(viewerOf({ type: "svg", kind: "file", media_type: "image/svg+xml" })).toBe("image");
    expect(viewerOf({ type: "html", kind: "file", media_type: "text/html" })).toBe("download");
    expect(filenameOf({ name: "deck", type: "pptx" })).toBe("deck.pptx");
    expect(filenameOf({ name: "chart.png", type: "png" })).toBe("chart.png");
  });

  it("names the runs that read a revision, from the hand-off receipts", () => {
    expect(readersOf(artifact("spec_markdown", 1), HAND_OFFS)).toEqual([{ run_id: "r9", stage: "implement" }]);
    expect(readersOf(artifact("spec_markdown", 2), HAND_OFFS)).toEqual([]);
  });

  it("reads a SARIF report as rows", () => {
    const rows = sarifRows(
      JSON.stringify({
        runs: [
          {
            tool: { driver: { name: "semgrep" } },
            results: [
              {
                ruleId: "sqli",
                level: "error",
                message: { text: "SQL from input" },
                locations: [{ physicalLocation: { artifactLocation: { uri: "a.py" }, region: { startLine: 3 } } }],
              },
            ],
          },
        ],
      }),
    );
    expect(rows).toEqual([{ tool: "semgrep", rule: "sqli", level: "error", message: "SQL from input", location: "a.py:3" }]);
  });

  it("renders markdown as elements, never as HTML", () => {
    const { container } = render(<MarkdownView text={"# Title\n\nSome **bold** and `code`.\n\n- one\n- two\n\n<img src=x onerror=alert(1)>"} />);
    expect(screen.getByRole("heading", { level: 3, name: "Title" })).toBeInTheDocument();
    expect(container.querySelector("strong")).toHaveTextContent("bold");
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(container.querySelector("img")).toBeNull();
    expect(container).toHaveTextContent("<img src=x onerror=alert(1)>");
  });

  it("shows the latest revision, its producer and readers, and switches revisions", async () => {
    vi.mocked(api.artifactContent).mockImplementation(async (_id, ref) =>
      new Blob([ref.endsWith("@1") ? "# First" : "# Second"], { type: "text/markdown" }),
    );
    renderArtifacts([artifact("spec_markdown", 1), artifact("spec_markdown", 2)]);
    expect(await screen.findByRole("heading", { name: "Second" })).toBeInTheDocument();
    expect(screen.getByTestId("artifact-producer")).toHaveTextContent("Produced by refine · attempt 1 · agent analyst@3");
    fireEvent.change(screen.getByLabelText("revision"), { target: { value: "1" } });
    expect(await screen.findByRole("heading", { name: "First" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("artifact-reader")).toHaveAttribute("href", "/p/billing-api/runs/r9"));
  });

  it("previews a PDF, plays a video, offers the download of an office file it cannot read", async () => {
    expect(viewerOf({ type: "pdf", kind: "file", media_type: "application/pdf" })).toBe("pdf");
    expect(viewerOf({ type: "mp4", kind: "file", media_type: "video/mp4" })).toBe("video");
    vi.mocked(api.artifactContent).mockResolvedValue(new Blob(["x"], { type: "application/octet-stream" }));
    const file = (name: string, type: Artifact["type"], media_type: string) =>
      artifact(name, 1, { type, kind: "file", media_type, storage_url: `s3://choregos/${name}` });
    renderArtifacts([
      file("report", "pdf", "application/pdf"),
      file("demo", "mp4", "video/mp4"),
      file("deck", "pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    ]);
    expect(await screen.findByTestId("office-refused")).toHaveTextContent("not an Office file");
    expect(await screen.findByTestId("artifact-download-only")).toHaveTextContent("download it to read it");
    expect(screen.getByTestId("artifact-download")).toHaveAttribute("download", "deck.pptx");
    fireEvent.click(screen.getByRole("tab", { name: "demo" }));
    expect(await screen.findByTestId("artifact-video")).toHaveAttribute("controls");
    fireEvent.click(screen.getByRole("tab", { name: "report" }));
    expect(await screen.findByTestId("artifact-pdf")).toHaveAttribute("src", "blob:artifact");
  });

  it("says a refused read instead of showing anything", async () => {
    vi.mocked(api.artifactContent).mockRejectedValue(new ApiError(409, { detail: "its content no longer matches its digest" }));
    renderArtifacts([artifact("budget", 1)]);
    const refused = await screen.findByTestId("artifact-refused");
    expect(refused).toHaveTextContent("no longer matches its digest");
    expect(within(screen.getByRole("tabpanel")).queryByTestId("markdown-view")).toBeNull();
  });

  it("orders the tabs as the steps produced them, and opens the latest document, not a word", () => {
    const at = (day: number) => `2026-11-0${day}T10:00:00Z`;
    const groups = groupArtifacts([
      artifact("allowed_paths", 1, { created_at: at(1), size: 13, kind: "json", type: "json" }),
      artifact("verdict", 1, { created_at: at(4), size: 7 }),
      artifact("spec_markdown", 1, { created_at: at(2), size: 3000 }),
      artifact("test_report", 1, { created_at: at(3), size: 1700 }),
    ]);
    expect(groups.map((g) => g.name)).toEqual(["allowed_paths", "spec_markdown", "test_report", "verdict"]);
    expect(firstToOpen(groups)?.name).toBe("test_report");
    expect(firstToOpen(groupArtifacts([artifact("verdict", 1, { size: 7 })]))?.name).toBe("verdict");
    expect(firstToOpen([])).toBeUndefined();
  });

  it("reads a one-word value above the tabs, not in one of them", async () => {
    vi.mocked(api.artifactContent).mockResolvedValue(new Blob(["# Spec"], { type: "text/markdown" }));
    const level: Artifact["display"] = { type: "level", show: "tab", levels: [{ value: "approve", tone: "ok" }] };
    renderArtifacts([artifact("verdict", 1, { value: "approve", display: level }), artifact("spec_markdown", 1, { size: 900 })]);
    expect(screen.getByTestId("artifact-values")).toHaveTextContent("Approve");
    expect(screen.queryByRole("tab", { name: "verdict" })).toBeNull();
    expect(await screen.findByRole("heading", { name: "Spec" })).toBeInTheDocument();
  });

  it("does not repeat what the facts card shows, nor what the workflow hides", () => {
    const facts: Artifact["display"] = { type: "level", show: "facts", levels: [{ value: "S" }] };
    const hidden: Artifact["display"] = { type: "markdown", show: "hidden" };
    renderArtifacts([artifact("severity", 1, { value: "S", display: facts }), artifact("notes", 1, { size: 900, display: hidden })]);
    expect(screen.queryByTestId("artifact-values")).toBeNull();
    expect(screen.queryByRole("tab")).toBeNull();
  });

  it("opens the tab a fact of the ticket asked for", async () => {
    vi.mocked(api.artifactContent).mockResolvedValue(new Blob(["# Other"], { type: "text/markdown" }));
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Artifacts
          itemId="w1"
          slug="billing-api"
          opened="b"
          handOffs={[]}
          artifacts={[artifact("a", 1, { size: 900, created_at: "2026-11-09T00:00:00Z" }), artifact("b", 1, { size: 900 })]}
        />
      </QueryClientProvider>,
    );
    expect(screen.getByRole("tab", { name: "b" })).toHaveAttribute("aria-selected", "true");
  });

  it("keeps what a step proposed for a ticket's size, which a person may have changed", () => {
    const facts: Artifact["display"] = { type: "level", show: "facts", title: "Size", levels: [{ value: "S" }, { value: "M" }] };
    renderArtifacts([artifact("size", 1, { value: "S", display: facts })]);
    expect(screen.getByTestId("artifact-value-row-size")).toHaveTextContent("SizeS");
  });
});
