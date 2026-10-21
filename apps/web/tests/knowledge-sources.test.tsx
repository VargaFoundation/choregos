import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { KnowledgeSources } from "@/components/knowledge-sources";
import { api } from "@/lib/api";
import { nextSyncLabel, pageIdsOf, refreshBody, refreshLabel, sourceConfig, syncSummary } from "@/lib/knowledge";
import type { KnowledgeSource, OrgConnector } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, orgConnectors: vi.fn(), createKnowledgeSource: vi.fn(), updateKnowledgeSource: vi.fn(), syncKnowledgeBase: vi.fn(), syncKnowledgeSource: vi.fn(), deleteKnowledgeSource: vi.fn() } };
});

const source: KnowledgeSource = {
  id: "src-1",
  kind: "sharepoint",
  name: "Architecture site",
  connector: "sharepoint",
  config: { site_id: "acme" },
  interval_minutes: 60,
  status: "failed",
  last_synced_at: "2026-10-13T08:00:00Z",
  last_error: "UpstreamError: token refused (401)",
  last_summary: { added: 2, updated: 0, deleted: 1, unchanged: 3, skipped: 0 },
  document_count: 4,
};

const connector = (name: string, kind: string, type: string) => ({ name, kind, type, operations: [] }) as unknown as OrgConnector;
const view = (sources: KnowledgeSource[] = [], onChange = vi.fn()) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <KnowledgeSources org="varga" slug="framework" sources={sources} onChange={onChange} />
    </QueryClientProvider>,
  );

describe("what a source says", () => {
  it("summarises a sync and builds its configuration", () => {
    expect(syncSummary({ added: 2, deleted: 1, unchanged: 9 })).toBe("2 added · 1 deleted");
    expect(syncSummary({ unchanged: 3 })).toBe("no change");
    expect(syncSummary(null)).toBe("never synced");
    const fields = { site: "b!drive", folder: "Policies", url: "https://git.test/r.git", ref: "", paths: "docs/*.md, *.md" };
    expect(sourceConfig("sharepoint", fields)).toEqual({ drive_id: "b!drive", folder: "Policies" });
    expect(sourceConfig("web", { ...fields, exclude: "/es/*, /pt/*" })).toEqual({
      url: "https://git.test/r.git", max_depth: 2, max_pages: 50, exclude: ["/es/*", "/pt/*"],
    });
    expect(sourceConfig("web", fields)).toEqual({ url: "https://git.test/r.git", max_depth: 2, max_pages: 50 });
    expect(sourceConfig("git", fields)).toEqual({ url: "https://git.test/r.git", ref: "main", paths: ["docs/*.md", "*.md"] });
  });
});

describe("the sources of a base (S33-04)", () => {
  beforeEach(() => {
    vi.mocked(api.orgConnectors).mockResolvedValue([]);
  });

  it("shows when each last synced, what it did and why it failed; syncs on demand", async () => {
    vi.mocked(api.syncKnowledgeSource).mockResolvedValue(source);
    const onChange = vi.fn();
    vi.mocked(api.orgConnectors).mockResolvedValue([]);
    view([source], onChange);
    const list = screen.getByTestId("knowledge-sources");
    expect(list).toHaveTextContent("Architecture site");
    expect(list).toHaveTextContent("2 added · 1 deleted");
    expect(list).toHaveTextContent("token refused (401)");
    fireEvent.click(screen.getByRole("button", { name: "sync now" }));
    await waitFor(() => expect(api.syncKnowledgeSource).toHaveBeenCalledWith("varga", "framework", "src-1"));
    expect(onChange).toHaveBeenCalled();
  });

  it("adds a SharePoint source through its connector", async () => {
    vi.mocked(api.createKnowledgeSource).mockResolvedValue(source);
    vi.mocked(api.orgConnectors).mockResolvedValue([connector("sharepoint", "documents", "sharepoint")]);
    view();
    await screen.findByRole("option", { name: "sharepoint" });
    fireEvent.change(screen.getByLabelText("documents connector"), { target: { value: "sharepoint" } });
    fireEvent.change(screen.getByLabelText("site or drive id"), { target: { value: "acme.sharepoint.com,1,2" } });
    fireEvent.click(screen.getByRole("button", { name: "add source" }));
    await waitFor(() =>
      expect(api.createKnowledgeSource).toHaveBeenCalledWith("varga", "framework", {
        kind: "sharepoint",
        name: "SharePoint / OneDrive",
        connector: "sharepoint",
        config: { site_id: "acme.sharepoint.com,1,2" },
        refresh_mode: "interval",
        interval_minutes: 60,
      }),
    );
  });

  it("offers only the documents/sharepoint connectors, as a list to pick from", async () => {
    vi.mocked(api.orgConnectors).mockResolvedValue([
      connector("diametral", "documents", "sharepoint"),
      connector("entra-acme", "identity", "entra"),
      connector("wiki", "documents", "confluence"),
    ]);
    view();
    await screen.findByRole("option", { name: "diametral" });
    const list = screen.getByLabelText("documents connector");
    expect(list.tagName).toBe("SELECT");
    expect([...list.querySelectorAll("option")].map((o) => o.textContent)).toEqual(["documents connector…", "diametral"]);
  });

  it("says so, with a link to declare one, when the organisation has none", async () => {
    vi.mocked(api.orgConnectors).mockResolvedValue([connector("entra-acme", "identity", "entra")]);
    view();
    const note = await screen.findByTestId("no-documents-connector");
    expect(screen.queryByLabelText("documents connector")).toBeNull();
    expect(within(note).getByRole("link")).toHaveAttribute("href", "/admin/connectors?type=documents/sharepoint");
  });

  it("says how each source refreshes and when it syncs next", () => {
    const cron: KnowledgeSource = {
      ...source,
      refresh_mode: "cron",
      refresh_cron: "0 6 * * 1-5",
      refresh_timezone: "Europe/Paris",
      next_sync_at: new Date(Date.now() + 23 * 60_000).toISOString(),
    };
    view([cron]);
    expect(screen.getByTestId("knowledge-sources")).toHaveTextContent("cron 0 6 * * 1-5 (Europe/Paris) · next in 23 minutes");
    expect(refreshLabel({ ...source, refresh_mode: "manual" })).toBe("manual");
    expect(nextSyncLabel({ refresh_mode: "manual" })).toBe("—");
    expect(refreshBody({ mode: "manual", interval: "5", cron: "", timezone: "UTC" })).toEqual({ refresh_mode: "manual" });
  });

  it("edits the refresh of a source, or of all of them", async () => {
    vi.mocked(api.updateKnowledgeSource).mockResolvedValue(source);
    const other = { ...source, id: "src-2", name: "Other" };
    view([source, other]);
    fireEvent.click(screen.getAllByRole("button", { name: "edit refresh" })[0]!);
    fireEvent.change(within(screen.getByRole("form", { name: "edit refresh" })).getByLabelText("refresh mode"), { target: { value: "manual" } });
    fireEvent.click(screen.getByRole("button", { name: "save refresh" }));
    await waitFor(() =>
      expect(api.updateKnowledgeSource).toHaveBeenCalledWith("varga", "framework", "src-1", { refresh_mode: "manual" }),
    );
    expect(api.updateKnowledgeSource).toHaveBeenCalledTimes(1);

    vi.mocked(api.updateKnowledgeSource).mockClear();
    fireEvent.click(screen.getAllByRole("button", { name: "edit refresh" })[0]!);
    fireEvent.change(within(screen.getByRole("form", { name: "edit refresh" })).getByLabelText("refresh mode"), { target: { value: "manual" } });
    fireEvent.click(screen.getByLabelText("apply to all sources"));
    fireEvent.click(screen.getByRole("button", { name: "save refresh" }));
    await waitFor(() => expect(api.updateKnowledgeSource).toHaveBeenCalledTimes(2));
  });

  it("shows the API's reason when a cron is refused", async () => {
    vi.mocked(api.updateKnowledgeSource).mockRejectedValue(new Error("`*/1 * * * *` fires every 1 min: the minimum is 5"));
    view([source]);
    fireEvent.click(screen.getByRole("button", { name: "edit refresh" }));
    fireEvent.change(within(screen.getByRole("form", { name: "edit refresh" })).getByLabelText("refresh mode"), { target: { value: "cron" } });
    fireEvent.change(within(screen.getByRole("form", { name: "edit refresh" })).getByLabelText("cron expression"), { target: { value: "*/1 * * * *" } });
    fireEvent.click(screen.getByRole("button", { name: "save refresh" }));
    expect(await screen.findByText(/the minimum is 5/)).toBeInTheDocument();
  });

  it("syncs a whole base at once", async () => {
    vi.mocked(api.syncKnowledgeBase).mockResolvedValue({} as never);
    view([source]);
    fireEvent.click(screen.getByRole("button", { name: "sync all now" }));
    await waitFor(() => expect(api.syncKnowledgeBase).toHaveBeenCalledWith("varga", "framework"));
  });

  it("adds a Confluence space or page tree through its connector (S39-20)", async () => {
    vi.mocked(api.createKnowledgeSource).mockResolvedValue(source);
    vi.mocked(api.orgConnectors).mockResolvedValue([
      connector("diametral", "documents", "sharepoint"),
      connector("wiki", "documents", "confluence"),
    ]);
    view();
    fireEvent.change(screen.getByLabelText("source kind"), { target: { value: "confluence" } });
    await screen.findByRole("option", { name: "wiki" });
    expect(screen.queryByRole("option", { name: "diametral" })).toBeNull();
    expect(screen.queryByLabelText("site or drive id")).toBeNull();
    fireEvent.change(screen.getByLabelText("documents connector"), { target: { value: "wiki" } });
    fireEvent.change(screen.getByLabelText("space key"), { target: { value: "ENG" } });
    fireEvent.change(screen.getByLabelText("page ids"), { target: { value: " 12345 " } });
    fireEvent.click(screen.getByRole("button", { name: "add source" }));
    await waitFor(() =>
      expect(api.createKnowledgeSource).toHaveBeenCalledWith("varga", "framework", {
        kind: "confluence",
        name: "Confluence space or page tree",
        connector: "wiki",
        config: { space_key: "ENG", page_id: "12345", include_attachments: false },
        refresh_mode: "interval",
        interval_minutes: 60,
      }),
    );
    expect(screen.getByText(/everything the connector/)).toBeInTheDocument();
  });

  it("extracts page ids from numbers and pasted Confluence URLs (S42-09d)", () => {
    const url = "https://acme.atlassian.net/wiki/spaces/ENG/pages/555/Deploy+guide";
    expect(pageIdsOf(`12, ${url}  12\n https://acme.atlassian.net/wiki/pages/viewpage.action?pageId=77 junk`)).toEqual(["12", "555", "77"]);
    expect(pageIdsOf("")).toEqual([]);
    const base = { site: "", folder: "", url: "", ref: "", paths: "", space: "ENG" };
    expect(sourceConfig("confluence", { ...base, pageId: `${url}, 7`, descendants: false })).toEqual({
      space_key: "ENG", page_ids: ["555", "7"], descendants: false, include_attachments: false,
    });
    expect(sourceConfig("confluence", { ...base, pageId: url })).toEqual({ space_key: "ENG", page_id: "555", include_attachments: false });
    expect(sourceConfig("confluence", { ...base, pageId: "", descendants: false })).toEqual({ space_key: "ENG", include_attachments: false });
  });

  it("sends the chosen pages without their sub-pages when the box is cleared", async () => {
    vi.mocked(api.createKnowledgeSource).mockResolvedValue(source);
    vi.mocked(api.orgConnectors).mockResolvedValue([connector("wiki", "documents", "confluence")]);
    view();
    fireEvent.change(screen.getByLabelText("source kind"), { target: { value: "confluence" } });
    await screen.findByRole("option", { name: "wiki" });
    fireEvent.change(screen.getByLabelText("documents connector"), { target: { value: "wiki" } });
    fireEvent.change(screen.getByLabelText("space key"), { target: { value: "ENG" } });
    fireEvent.change(screen.getByLabelText("page ids"), { target: { value: "1, 2" } });
    fireEvent.click(screen.getByLabelText("and its sub-pages"));
    fireEvent.click(screen.getByRole("button", { name: "add source" }));
    await waitFor(() =>
      expect(api.createKnowledgeSource).toHaveBeenCalledWith(
        "varga",
        "framework",
        expect.objectContaining({ config: { space_key: "ENG", page_ids: ["1", "2"], descendants: false, include_attachments: false } }),
      ),
    );
  });

  it("sends no page id for a whole space, and asks for attachments when ticked", () => {
    const base = { site: "", folder: "", url: "", ref: "", paths: "" };
    expect(sourceConfig("confluence", { ...base, space: "ENG", pageId: "" })).toEqual({ space_key: "ENG", include_attachments: false });
    expect(sourceConfig("confluence", { ...base, space: "ENG", attachments: true })).toEqual({ space_key: "ENG", include_attachments: true });
  });

  it("adds a git source, through a git connector when the repository is private", async () => {
    vi.mocked(api.createKnowledgeSource).mockResolvedValue(source);
    vi.mocked(api.orgConnectors).mockResolvedValue([connector("acme-github", "documents", "git")]);
    view();
    fireEvent.change(screen.getByLabelText("source kind"), { target: { value: "git" } });
    fireEvent.change(screen.getByLabelText("url"), { target: { value: "https://github.com/acme/private.git" } });
    await screen.findByRole("option", { name: "acme-github" });
    fireEvent.change(screen.getByLabelText("git connector"), { target: { value: "acme-github" } });
    fireEvent.click(screen.getByRole("button", { name: "add source" }));
    await waitFor(() =>
      expect(api.createKnowledgeSource).toHaveBeenCalledWith(
        "varga",
        "framework",
        expect.objectContaining({ kind: "git", connector: "acme-github" }),
      ),
    );
  });
});
