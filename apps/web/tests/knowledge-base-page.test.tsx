import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { Suspense } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import KnowledgeBasePage from "@/app/knowledge/[slug]/page";
import { api } from "@/lib/api";
import { SERVING_MODES, baseHealth, indexLine, readersLabel, servedRunsLabel } from "@/lib/knowledge";
import type { KnowledgeBase, KnowledgeDocument, KnowledgeSource } from "@/lib/types";

vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "varga" }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, knowledgeBase: vi.fn(), orgConnectors: vi.fn(), members: vi.fn(), projects: vi.fn() } };
});

const doc = (id: string, status: KnowledgeDocument["status"], extra: Partial<KnowledgeDocument> = {}): KnowledgeDocument => ({
  id,
  filename: `${id}.md`,
  title: `Document ${id}`,
  mime: "text/markdown",
  size_bytes: 2048,
  digest: `sha256:${id}`,
  source: "upload",
  status,
  ...extra,
});

const source = (extra: Partial<KnowledgeSource> = {}): KnowledgeSource => ({
  id: "src-1",
  kind: "web",
  name: "Public handbook",
  connector: null,
  config: { url: "https://example.com/handbook" },
  interval_minutes: 60,
  status: "idle",
  last_synced_at: "2026-11-01T08:00:00Z",
  last_summary: { added: 1 },
  document_count: 1,
  ...extra,
});

const base: KnowledgeBase = {
  slug: "handbook",
  name: "Handbook",
  scope: "org",
  projects: [],
  groups: [],
  serving_mode: "files",
  status: "active",
  document_count: 2,
  documents_by_status: { ready: 2 },
  documents: [doc("a", "ready"), doc("b", "ready")],
  sources: [],
  restricted: false,
  can: { edit: true, distill: true, share: true, archive: true, widen: true },
  my_role: "owner",
  grants: [],
  used_by: [],
};

async function show(value: KnowledgeBase) {
  vi.mocked(api.knowledgeBase).mockResolvedValue(value);
  await act(async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <Suspense fallback="…">
          <KnowledgeBasePage params={Promise.resolve({ slug: value.slug })} />
        </Suspense>
      </QueryClientProvider>,
    );
  });
  return screen.findByTestId("knowledge-status");
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.orgConnectors).mockResolvedValue([]);
  vi.mocked(api.members).mockResolvedValue([]);
  vi.mocked(api.projects).mockResolvedValue({ items: [] } as never);
});

describe("whether the base can be used now", () => {
  it("is ready when every document is converted and no source failed", () => {
    expect(baseHealth(base)).toMatchObject({ state: "ready", tone: "ok", title: "Ready" });
    expect(baseHealth({ ...base, serving_mode: "index" }).detail).toMatch(/searched as an index/);
  });

  it("is converting while documents wait or convert, with how many are ready out of all", () => {
    const health = baseHealth({ ...base, documents_by_status: { ready: 3, pending: 1, converting: 1 } });
    expect(health).toMatchObject({ state: "converting", tone: "running", title: "Converting: 3 of 5 documents ready" });
    expect(health.progress).toEqual({ done: 3, total: 5 });
    expect(health.detail).toMatch(/3 ready documents are served/);
    expect(baseHealth({ ...base, sources: [source({ status: "syncing" })] })).toMatchObject({
      state: "converting",
      title: "Syncing Public handbook",
    });
  });

  it("needs attention for a failed document or a source whose sync failed, worst first", () => {
    const failed = baseHealth({ ...base, documents_by_status: { ready: 2, failed: 1, converting: 1 } });
    expect(failed).toMatchObject({ state: "attention", tone: "danger", title: "Needs attention" });
    expect(failed.detail).toMatch(/^1 document failed to convert\. The 2 ready documents are served/);
    const broken = baseHealth({ ...base, sources: [source({ status: "failed", last_error: "401" })] });
    expect(broken.detail).toMatch(/1 source failed to sync \(Public handbook\)/);
  });

  it("is empty with the next step, and archived serves nothing", () => {
    expect(baseHealth({ ...base, documents_by_status: {} })).toMatchObject({ state: "empty", tone: "neutral" });
    expect(baseHealth({ ...base, documents_by_status: {} }).detail).toMatch(/upload files or add a source/);
    expect(baseHealth({ ...base, status: "archived" })).toMatchObject({ state: "archived", title: "Archived" });
  });

  it("the page opens on the banner, in the state's colour, with a progress bar while converting", async () => {
    const banner = await show({ ...base, documents_by_status: { ready: 1, converting: 1 }, documents: [doc("a", "ready"), doc("b", "converting")] });
    expect(banner).toHaveAttribute("data-state", "converting");
    expect(banner.className).toContain("bg-running-soft");
    expect(within(banner).getByTestId("knowledge-status-title")).toHaveTextContent("Converting: 1 of 2 documents ready");
    expect(within(banner).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "1");
  });

  it("a failed document turns the banner red", async () => {
    const banner = await show({
      ...base,
      documents_by_status: { ready: 1, failed: 1 },
      documents: [doc("a", "ready"), doc("b", "failed", { error: "the document holds no text" })],
    });
    expect(banner).toHaveAttribute("data-state", "attention");
    expect(banner.className).toContain("bg-failed-soft");
    expect(banner).toHaveTextContent("1 document failed to convert");
  });
});

describe("who uses it", () => {
  it("names the agents whose current revision reads it, each a link", async () => {
    await show({ ...base, used_by: [{ agent: "support", display_name: "Support desk", version: 3 }] });
    const agents = screen.getByTestId("knowledge-agents");
    expect(within(agents).getByRole("link", { name: /Support desk/ })).toHaveAttribute("href", "/agents/support");
    expect(agents).toHaveTextContent("v3");
    expect(screen.getByTestId("knowledge-used-by")).toHaveTextContent(
      "A run is served this base only when its agent names it. A direct conversation with an agent is not.",
    );
  });

  it("says when no agent reads it, and where to name it", async () => {
    await show(base);
    const none = screen.getByTestId("knowledge-no-agent");
    expect(none).toHaveTextContent("No agent reads it yet");
    expect(within(none).getByRole("link")).toHaveAttribute("href", "/agents");
  });

  it("says which runs may be served it, and never a group's", () => {
    expect(servedRunsLabel(base)).toBe("Runs of every project of the organisation.");
    expect(servedRunsLabel({ ...base, scope: "projects", projects: ["billing-api"] })).toBe("Runs of project billing-api.");
    expect(servedRunsLabel({ ...base, scope: "groups" })).toMatch(/^No run/);
    expect(servedRunsLabel({ ...base, restricted: true })).toMatch(/only for a work item started by someone who reads it/);
  });
});

describe("who reads it", () => {
  it("an organisation base needs an organisation role: members of projects only do not read it", () => {
    expect(readersLabel(base)).toEqual({
      open: true,
      text: "Everyone with a role in the organisation. People who only belong to projects do not read it, unless granted a role below.",
    });
  });

  it("a project base, a group base and a restricted one, said precisely", () => {
    expect(readersLabel({ ...base, scope: "projects", projects: ["billing-api", "checkout-web"] }).text).toBe(
      "The members of projects billing-api, checkout-web, and everyone with a role in the organisation.",
    );
    expect(readersLabel({ ...base, scope: "groups", groups: ["auditors"] }).text).toBe(
      "People in the identity-provider group auditors who also hold a role in the organisation.",
    );
    expect(readersLabel({ ...base, restricted: true })).toEqual({
      open: false,
      text: "Only the people granted a role below, and the organisation's administrators.",
    });
  });

  it("an open base wears a green badge, a restricted one an amber lock", async () => {
    await show({ ...base, restricted: true });
    const readers = screen.getByTestId("knowledge-readers");
    expect(within(readers).getByText("Restricted").className).toContain("text-waiting-ink");
    expect(screen.getByTestId("knowledge-readable")).toHaveTextContent("Only the people granted a role below");
    expect(screen.getByTestId("knowledge-access")).not.toHaveTextContent("readable by whole organisation");
  });

  it("the grant editor shows each role as a badge with what it lets one do", async () => {
    await show(base);
    const legend = screen.getByTestId("knowledge-role-legend");
    expect(legend).toHaveTextContent("viewer");
    expect(legend).toHaveTextContent("editor");
    expect(legend).toHaveTextContent("feeds, distils, shares and archives");
  });
});

describe("how runs read it", () => {
  it("says both modes as what they do, the 200k tokens as advice, and never as a limit", () => {
    expect(SERVING_MODES.files.text).toMatch(/copied into the run's workspace/);
    expect(SERVING_MODES.files.text).toMatch(/below about 200k tokens/);
    expect(SERVING_MODES.index.text).toMatch(/No size ceiling/);
    expect(SERVING_MODES.index.text).not.toMatch(/200k/);
  });

  it("shows both options, marks the current one, and the index in one line", async () => {
    await show({ ...base, serving_mode: "index", index: { chunks: 230, documents: 14, mode: "hybrid" } });
    const serving = screen.getByTestId("knowledge-serving");
    expect(serving).not.toHaveTextContent("recommended above");
    expect(screen.getByTestId("serving-index")).toHaveTextContent("Index · current");
    expect(screen.getByTestId("serving-files")).not.toHaveTextContent("current");
    expect(screen.getByTestId("knowledge-index-line")).toHaveTextContent("Index: 14 documents in 230 passages · hybrid search");
    expect(indexLine({ chunks: 1, documents: 1, mode: "full-text" })).toBe("1 document in 1 passage · full-text search only");
  });

  it("a reader cannot switch the mode, and is told why", async () => {
    await show({ ...base, can: { edit: false, distill: false, share: false, archive: false, widen: false }, cannot_reason: "Viewers read only." });
    expect(screen.getByLabelText("serving mode")).toBeDisabled();
    expect(screen.getByLabelText("serving mode")).toHaveAttribute("title", "Viewers read only.");
  });
});

describe("the documents", () => {
  const mixed: KnowledgeBase = {
    ...base,
    documents_by_status: { ready: 2, failed: 1, pending: 1 },
    documents: [doc("a", "ready"), doc("b", "ready"), doc("c", "failed", { error: "no text" }), doc("d", "pending")],
  };

  it("colours each status and counts them in filters", async () => {
    await show(mixed);
    const table = screen.getByTestId("knowledge-documents");
    expect(within(table).getByText("Failed").className).toContain("text-failed-ink");
    expect(within(table).getAllByText("Ready")[0]!.className).toContain("text-succeeded-ink");
    expect(within(table).getByText("Waiting").className).toContain("text-running-ink");
    const filters = screen.getByTestId("knowledge-document-filter");
    expect(within(filters).getByRole("button", { name: /All\s*4/ })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(within(filters).getByRole("button", { name: /Failed\s*1/ }));
    expect(screen.getByTestId("knowledge-documents").querySelectorAll("tbody tr")).toHaveLength(1);
    expect(screen.getByTestId("knowledge-documents")).toHaveTextContent("no text");
  });

  it("deleting is said in the danger tone, and the main action of the card is the upload", async () => {
    await show(mixed);
    expect(screen.getAllByRole("button", { name: "Delete" })[0]!.className).toContain("text-failed-ink");
    expect(screen.getByLabelText("Documents to upload").closest("label")!.className).toContain("bg-inverse");
  });
});
