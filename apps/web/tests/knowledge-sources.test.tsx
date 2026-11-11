import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { KnowledgeSources } from "@/components/knowledge-sources";
import { api } from "@/lib/api";
import { describeRefresh, draftOf, firstUsableKind, intervalLabel, headersOf, nextSyncLabel, pageIdsOf, refreshBody, refreshLabel, sourceConfig, sourceState, syncSummary } from "@/lib/knowledge";
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
/** The form is behind a button: the existing sources come first. */
const adding = (sources: KnowledgeSource[] = [], onChange = vi.fn()) => {
  const rendered = view(sources, onChange);
  fireEvent.click(screen.getByRole("button", { name: "Add a source" }));
  return rendered;
};

describe("what a source says", () => {
  it("summarises a sync and builds its configuration", () => {
    expect(syncSummary({ added: 2, deleted: 1, unchanged: 9 })).toBe("2 added · 1 deleted");
    expect(syncSummary({ unchanged: 3 })).toBe("no change");
    expect(syncSummary(null)).toBe("never synced");
    expect(syncSummary({ added: 1, truncated: 2 })).toBe("1 added · 2 truncated");
    const fields = { site: "b!drive", folder: "Policies", url: "https://git.test/r.git", ref: "", paths: "docs/*.md, *.md" };
    expect(sourceConfig("sharepoint", fields)).toEqual({ drive_id: "b!drive", folder: "Policies" });
    expect(sourceConfig("sharepoint", { ...fields, exclude: "/Z-*/*, /*DO NOT USE*" })).toEqual({
      drive_id: "b!drive", folder: "Policies", exclude: ["/Z-*/*", "/*DO NOT USE*"],
    });
    expect(sourceConfig("web", { ...fields, exclude: "/es/*, /pt/*" })).toEqual({
      url: "https://git.test/r.git", max_depth: 2, max_pages: 50, exclude: ["/es/*", "/pt/*"],
    });
    expect(sourceConfig("web", fields)).toEqual({ url: "https://git.test/r.git", max_depth: 2, max_pages: 50 });
    expect(sourceConfig("web", { ...fields, pageOnly: true, depth: "4", maxPages: "300" })).toEqual({
      url: "https://git.test/r.git", max_depth: 0, max_pages: 1,
    });
    expect(sourceConfig("web", { ...fields, depth: "9", maxPages: "120", documents: true })).toEqual({
      url: "https://git.test/r.git", max_depth: 5, max_pages: 120, include_documents: true,
    });
    expect(sourceConfig("git", fields)).toEqual({ url: "https://git.test/r.git", ref: "main", paths: ["docs/*.md", "*.md"] });
    expect(sourceConfig("web", { ...fields, headers: "Accept-Language: fr\nnot a header\n X-Env : prod " })).toEqual({
      url: "https://git.test/r.git", max_depth: 2, max_pages: 50, headers: { "Accept-Language": "fr", "X-Env": "prod" },
    });
    expect(headersOf("")).toEqual({});
  });
});

describe("the sources of a base (S33-04)", () => {
  beforeEach(() => {
    vi.mocked(api.orgConnectors).mockResolvedValue([]);
  });

  it("names the documents a sync converted with rows cut (S42-09e)", () => {
    const cut = { ...source, last_summary: { added: 1, truncated: 1, truncated_reasons: ['book.xlsx: Sheet "Q3": 5 more rows not shown (the first 2000 are kept)'] } };
    view([cut]);
    expect(screen.getByTestId("source-truncations-src-1")).toHaveTextContent("book.xlsx: Sheet \"Q3\": 5 more rows not shown");
    expect(screen.getByTestId("knowledge-sources")).toHaveTextContent("1 added · 1 truncated");
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
    fireEvent.click(screen.getByRole("button", { name: "Sync now" }));
    await waitFor(() => expect(api.syncKnowledgeSource).toHaveBeenCalledWith("varga", "framework", "src-1"));
    expect(onChange).toHaveBeenCalled();
  });

  it("adds a single page, or a crawl with linked documents (S42-09b)", async () => {
    vi.mocked(api.createKnowledgeSource).mockResolvedValue(source);
    adding();
    fireEvent.change(screen.getByLabelText("source kind"), { target: { value: "web" } });
    fireEvent.change(screen.getByLabelText("url"), { target: { value: "https://example.com/offer" } });
    fireEvent.click(screen.getByLabelText("This page only (no links followed)"));
    expect(screen.queryByLabelText("crawl depth")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Add source" }));
    await waitFor(() =>
      expect(api.createKnowledgeSource).toHaveBeenCalledWith(
        "varga",
        "framework",
        expect.objectContaining({ kind: "web", config: { url: "https://example.com/offer", max_depth: 0, max_pages: 1 } }),
      ),
    );
    // Added, the form closed; opened again, it starts afresh: a crawl, not a single page.
    await waitFor(() => expect(screen.queryByRole("form", { name: "Add a source" })).toBeNull());
    fireEvent.click(screen.getByRole("button", { name: "Add a source" }));
    fireEvent.change(screen.getByLabelText("url"), { target: { value: "https://example.com/offer" } });
    expect(screen.getByLabelText("This page only (no links followed)")).not.toBeChecked();
    fireEvent.change(screen.getByLabelText("crawl depth"), { target: { value: "1" } });
    fireEvent.click(screen.getByLabelText("Include linked documents (PDF, Word, PowerPoint, Excel)"));
    fireEvent.click(screen.getByRole("button", { name: "Add source" }));
    await waitFor(() =>
      expect(api.createKnowledgeSource).toHaveBeenLastCalledWith(
        "varga",
        "framework",
        expect.objectContaining({ config: { url: "https://example.com/offer", max_depth: 1, max_pages: 50, include_documents: true } }),
      ),
    );
  });

  it("adds a SharePoint source through its connector", async () => {
    vi.mocked(api.createKnowledgeSource).mockResolvedValue(source);
    vi.mocked(api.orgConnectors).mockResolvedValue([connector("sharepoint", "documents", "sharepoint")]);
    adding();
    await screen.findByRole("option", { name: "sharepoint" });
    fireEvent.change(screen.getByLabelText("documents connector"), { target: { value: "sharepoint" } });
    fireEvent.change(screen.getByLabelText("site or drive id"), { target: { value: "acme.sharepoint.com,1,2" } });
    fireEvent.click(screen.getByRole("button", { name: "Add source" }));
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
    adding();
    await screen.findByRole("option", { name: "diametral" });
    const list = screen.getByLabelText("documents connector");
    expect(list.tagName).toBe("SELECT");
    expect([...list.querySelectorAll("option")].map((o) => o.textContent)).toEqual(["documents connector…", "diametral"]);
  });

  it("says so, with a link to declare one, when the organisation has none", async () => {
    vi.mocked(api.orgConnectors).mockResolvedValue([connector("entra-acme", "identity", "entra")]);
    adding();
    await screen.findByRole("option", { name: "SharePoint / OneDrive — no connector yet" });
    fireEvent.change(screen.getByLabelText("source kind"), { target: { value: "sharepoint" } });
    const note = await screen.findByTestId("no-documents-connector");
    expect(screen.queryByLabelText("documents connector")).toBeNull();
    expect(within(note).getByRole("link")).toHaveAttribute("href", "/admin/connectors?type=documents/sharepoint");
    expect(screen.getByRole("button", { name: "Add source" })).toBeDisabled();
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
    expect(screen.getByTestId("knowledge-sources")).toHaveTextContent(
      "every weekday (Monday to Friday) at 06:00 (Europe/Paris) · next in 23 minutes",
    );
    expect(refreshLabel({ ...source, refresh_mode: "manual" })).toBe("manual");
    expect(refreshLabel({ ...source, refresh_mode: "cron", refresh_cron: "*/15 9-17 * * *", refresh_timezone: "UTC" })).toBe(
      "cron */15 9-17 * * * (UTC)",
    );
    expect(nextSyncLabel({ refresh_mode: "manual" })).toBe("—");
    expect(refreshLabel({ ...source, refresh_mode: "interval", interval_minutes: 1440 })).toBe("every day");
    expect([15, 60, 360, 2880].map(intervalLabel)).toEqual(["every 15 minutes", "every hour", "every 6 hours", "every 2 days"]);
    expect(refreshBody({ ...draftOf(), mode: "manual" })).toEqual({ refresh_mode: "manual" });
  });

  it("edits the refresh of a source, or of all of them", async () => {
    vi.mocked(api.updateKnowledgeSource).mockResolvedValue(source);
    const other = { ...source, id: "src-2", name: "Other" };
    view([source, other]);
    fireEvent.click(screen.getAllByRole("button", { name: "Edit refresh" })[0]!);
    fireEvent.change(within(screen.getByRole("form", { name: "Edit refresh" })).getByLabelText("refresh mode"), { target: { value: "manual" } });
    fireEvent.click(screen.getByRole("button", { name: "Save refresh" }));
    await waitFor(() =>
      expect(api.updateKnowledgeSource).toHaveBeenCalledWith("varga", "framework", "src-1", { refresh_mode: "manual" }),
    );
    expect(api.updateKnowledgeSource).toHaveBeenCalledTimes(1);

    vi.mocked(api.updateKnowledgeSource).mockClear();
    fireEvent.click(screen.getAllByRole("button", { name: "Edit refresh" })[0]!);
    fireEvent.change(within(screen.getByRole("form", { name: "Edit refresh" })).getByLabelText("refresh mode"), { target: { value: "manual" } });
    fireEvent.click(screen.getByLabelText("apply to all sources"));
    fireEvent.click(screen.getByRole("button", { name: "Save refresh" }));
    await waitFor(() => expect(api.updateKnowledgeSource).toHaveBeenCalledTimes(2));
  });

  it("offers refreshes a person reads, stored as an interval or a cron (S47-48)", () => {
    const base = { ...draftOf(), timezone: "Europe/Paris" };
    expect(refreshBody({ ...base, mode: "hours", hours: "6" })).toEqual({ refresh_mode: "interval", interval_minutes: 360 });
    expect(refreshBody({ ...base, mode: "minutes", interval: "15" })).toEqual({ refresh_mode: "interval", interval_minutes: 15 });
    const cron = (patch: Partial<typeof base>) => refreshBody({ ...base, ...patch });
    expect(cron({ mode: "daily", time: "07:30" })).toEqual({ refresh_mode: "cron", refresh_cron: "30 7 * * *", refresh_timezone: "Europe/Paris" });
    expect(cron({ mode: "weekdays", time: "06:00" }).refresh_cron).toBe("0 6 * * 1-5");
    expect(cron({ mode: "weekly", time: "18:05", days: [4, 1, 4] }).refresh_cron).toBe("5 18 * * 1,4");
    expect(cron({ mode: "weekly", days: [] }).refresh_cron).toBe("0 6 * * 1");
    expect(cron({ mode: "monthly", dayOfMonth: "15", time: "02:00" }).refresh_cron).toBe("0 2 15 * *");
    expect(cron({ mode: "cron", cron: " */30 * * * * " }).refresh_cron).toBe("*/30 * * * *");
    expect(describeRefresh({ ...base, mode: "weekly", days: [1, 4], time: "18:05" })).toBe("every Monday and Thursday at 18:05");
    // A stored cron reads back as the choice that wrote it; any other stays a cron of one's own.
    const back = (refresh_cron: string) => draftOf({ refresh_mode: "cron", refresh_cron, refresh_timezone: "Europe/Paris" });
    expect(back("5 18 * * 1,4")).toMatchObject({ mode: "weekly", days: [1, 4], time: "18:05" });
    expect(back("0 2 15 * *")).toMatchObject({ mode: "monthly", dayOfMonth: "15", time: "02:00" });
    expect(back("*/15 9-17 * * *")).toMatchObject({ mode: "cron", cron: "*/15 9-17 * * *" });
    expect(draftOf({ refresh_mode: "interval", interval_minutes: 120 })).toMatchObject({ mode: "hours", hours: "2" });
    expect(draftOf({ refresh_mode: "interval", interval_minutes: 45 })).toMatchObject({ mode: "minutes", interval: "45" });
  });

  it("edits a refresh to chosen days of the week, and says it in words", async () => {
    vi.mocked(api.updateKnowledgeSource).mockResolvedValue(source);
    view([source]);
    fireEvent.click(screen.getByRole("button", { name: "Edit refresh" }));
    const form = within(screen.getByRole("form", { name: "Edit refresh" }));
    fireEvent.change(form.getByLabelText("refresh mode"), { target: { value: "weekly" } });
    expect(form.queryByLabelText("cron expression")).toBeNull();
    fireEvent.click(form.getByLabelText("Thu"));
    fireEvent.change(form.getByLabelText("time of day"), { target: { value: "18:30" } });
    fireEvent.change(form.getByLabelText("time zone"), { target: { value: "UTC" } });
    expect(form.getByTestId("refresh-summary")).toHaveTextContent("Syncs every Monday and Thursday at 18:30, UTC time.");
    fireEvent.click(screen.getByRole("button", { name: "Save refresh" }));
    await waitFor(() =>
      expect(api.updateKnowledgeSource).toHaveBeenCalledWith("varga", "framework", "src-1", {
        refresh_mode: "cron",
        refresh_cron: "30 18 * * 1,4",
        refresh_timezone: "UTC",
      }),
    );
  });

  it("shows the API's reason when a cron is refused", async () => {
    vi.mocked(api.updateKnowledgeSource).mockRejectedValue(new Error("`*/1 * * * *` fires every 1 min: the minimum is 5"));
    view([source]);
    fireEvent.click(screen.getByRole("button", { name: "Edit refresh" }));
    fireEvent.change(within(screen.getByRole("form", { name: "Edit refresh" })).getByLabelText("refresh mode"), { target: { value: "cron" } });
    fireEvent.change(within(screen.getByRole("form", { name: "Edit refresh" })).getByLabelText("cron expression"), { target: { value: "*/1 * * * *" } });
    fireEvent.click(screen.getByRole("button", { name: "Save refresh" }));
    expect(await screen.findByText(/the minimum is 5/)).toBeInTheDocument();
  });

  it("syncs a whole base at once", async () => {
    vi.mocked(api.syncKnowledgeBase).mockResolvedValue({} as never);
    view([source]);
    fireEvent.click(screen.getByRole("button", { name: "Sync all now" }));
    await waitFor(() => expect(api.syncKnowledgeBase).toHaveBeenCalledWith("varga", "framework"));
  });

  it("adds a Confluence space or page tree through its connector (S39-20)", async () => {
    vi.mocked(api.createKnowledgeSource).mockResolvedValue(source);
    vi.mocked(api.orgConnectors).mockResolvedValue([
      connector("diametral", "documents", "sharepoint"),
      connector("wiki", "documents", "confluence"),
    ]);
    adding();
    fireEvent.change(screen.getByLabelText("source kind"), { target: { value: "confluence" } });
    await screen.findByRole("option", { name: "wiki" });
    expect(screen.queryByRole("option", { name: "diametral" })).toBeNull();
    expect(screen.queryByLabelText("site or drive id")).toBeNull();
    fireEvent.change(screen.getByLabelText("documents connector"), { target: { value: "wiki" } });
    fireEvent.change(screen.getByLabelText("space key"), { target: { value: "ENG" } });
    fireEvent.change(screen.getByLabelText("page ids"), { target: { value: " 12345 " } });
    expect(screen.getByText(/everything the connector/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add source" }));
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
    // Added: the form closes, and the sources list is what the card shows again.
    await waitFor(() => expect(screen.queryByRole("form", { name: "Add a source" })).toBeNull());
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
    adding();
    fireEvent.change(screen.getByLabelText("source kind"), { target: { value: "confluence" } });
    await screen.findByRole("option", { name: "wiki" });
    fireEvent.change(screen.getByLabelText("documents connector"), { target: { value: "wiki" } });
    fireEvent.change(screen.getByLabelText("space key"), { target: { value: "ENG" } });
    fireEvent.change(screen.getByLabelText("page ids"), { target: { value: "1, 2" } });
    fireEvent.click(screen.getByLabelText("and its sub-pages"));
    fireEvent.click(screen.getByRole("button", { name: "Add source" }));
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
    adding();
    fireEvent.change(screen.getByLabelText("source kind"), { target: { value: "git" } });
    fireEvent.change(screen.getByLabelText("url"), { target: { value: "https://github.com/acme/private.git" } });
    await screen.findByRole("option", { name: "acme-github" });
    fireEvent.change(screen.getByLabelText("git connector"), { target: { value: "acme-github" } });
    fireEvent.click(screen.getByRole("button", { name: "Add source" }));
    await waitFor(() =>
      expect(api.createKnowledgeSource).toHaveBeenCalledWith(
        "varga",
        "framework",
        expect.objectContaining({ kind: "git", connector: "acme-github" }),
      ),
    );
  });

  it("reads a site behind a login through a documents/web connector, with plain headers of its own (S47-48)", async () => {
    vi.mocked(api.createKnowledgeSource).mockResolvedValue(source);
    vi.mocked(api.orgConnectors).mockResolvedValue([
      connector("intranet", "documents", "web"),
      connector("acme-github", "documents", "git"),
    ]);
    adding();
    fireEvent.change(screen.getByLabelText("source kind"), { target: { value: "web" } });
    fireEvent.change(screen.getByLabelText("url"), { target: { value: "https://intranet.example.com/docs/" } });
    await screen.findByRole("option", { name: "intranet" });
    expect(screen.queryByRole("option", { name: "acme-github" })).toBeNull();
    fireEvent.change(screen.getByLabelText("web connector"), { target: { value: "intranet" } });
    fireEvent.change(screen.getByLabelText("http headers"), { target: { value: "Accept-Language: fr" } });
    expect(screen.queryByTestId("secret-header")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Add source" }));
    await waitFor(() =>
      expect(api.createKnowledgeSource).toHaveBeenCalledWith(
        "varga",
        "framework",
        expect.objectContaining({
          kind: "web",
          connector: "intranet",
          config: { url: "https://intranet.example.com/docs/", max_depth: 2, max_pages: 50, headers: { "Accept-Language": "fr" } },
        }),
      ),
    );
  });

  it("flags a credential typed as a plain header, and sends an anonymous crawl without a connector", async () => {
    vi.mocked(api.createKnowledgeSource).mockResolvedValue(source);
    adding();
    fireEvent.change(screen.getByLabelText("source kind"), { target: { value: "web" } });
    fireEvent.change(screen.getByLabelText("url"), { target: { value: "https://example.com/" } });
    fireEvent.change(screen.getByLabelText("http headers"), { target: { value: "Authorization: Bearer abc" } });
    expect(screen.getByTestId("secret-header")).toHaveTextContent("Authorization looks like a credential");
    expect(await screen.findByTestId("no-web-connector")).toHaveAttribute("href", "/admin/connectors?type=documents/web");
    fireEvent.change(screen.getByLabelText("http headers"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Add source" }));
    await waitFor(() =>
      expect(api.createKnowledgeSource).toHaveBeenCalledWith("varga", "framework", expect.objectContaining({ kind: "web", connector: null })),
    );
  });
});

describe("a readable sources card", () => {
  beforeEach(() => {
    vi.mocked(api.orgConnectors).mockResolvedValue([]);
  });

  it("lists the sources first; the form to add one opens from a button, never by itself", async () => {
    view([{ ...source, status: "idle", last_error: null }]);
    expect(screen.getByTestId("knowledge-sources")).toHaveTextContent("Architecture site");
    expect(screen.queryByLabelText("source kind")).toBeNull();
    expect(screen.queryByTestId("no-documents-connector")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Add a source" }));
    expect(screen.getByRole("form", { name: "Add a source" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByLabelText("source kind")).toBeNull();
  });

  it("opens on a kind the organisation can sync: a web site without a connector, else the first connected kind it has", async () => {
    expect(firstUsableKind([])).toBe("web");
    expect(firstUsableKind([connector("wiki", "documents", "confluence")])).toBe("confluence");
    expect(firstUsableKind([connector("sp", "documents", "sharepoint"), connector("wiki", "documents", "confluence")])).toBe("sharepoint");
    expect(firstUsableKind([connector("entra-acme", "identity", "entra")])).toBe("web");
    adding();
    await screen.findByRole("option", { name: "SharePoint / OneDrive — no connector yet" });
    expect(screen.getByLabelText("source kind")).toHaveValue("web");
    expect(screen.queryByTestId("no-documents-connector")).toBeNull();
    expect(screen.getByRole("button", { name: "Add source" })).toBeEnabled();
  });

  it("opens on Confluence when that is the connector the organisation has", async () => {
    vi.mocked(api.orgConnectors).mockResolvedValue([connector("wiki", "documents", "confluence")]);
    adding();
    await screen.findByRole("option", { name: "wiki" });
    expect(screen.getByLabelText("source kind")).toHaveValue("confluence");
  });

  it("says each source's state in words and colour: up to date, syncing, failed with its error, never synced", () => {
    expect(sourceState({ status: "idle", last_synced_at: "2026-11-01T08:00:00Z", last_error: null })).toEqual({ label: "Up to date", tone: "ok" });
    expect(sourceState({ status: "syncing", last_synced_at: null, last_error: null })).toEqual({ label: "Syncing", tone: "running" });
    expect(sourceState({ status: "failed", last_synced_at: null, last_error: "401" })).toEqual({ label: "Sync failed", tone: "danger" });
    expect(sourceState({ status: "idle", last_synced_at: null, last_error: null })).toEqual({ label: "Never synced", tone: "neutral" });
    view([
      { ...source, id: "ok", status: "idle", last_error: null },
      { ...source, id: "busy", status: "syncing", last_error: null },
      source,
      { ...source, id: "new", status: "idle", last_error: null, last_synced_at: null, last_summary: null },
    ]);
    expect(screen.getByTestId("source-state-ok")).toHaveTextContent("Up to date");
    expect(screen.getByTestId("source-state-ok").className).toContain("text-succeeded-ink");
    expect(screen.getByTestId("source-state-busy").className).toContain("text-running-ink");
    expect(screen.getByTestId("source-state-src-1")).toHaveTextContent("Sync failed");
    expect(screen.getByTestId("source-state-src-1").className).toContain("text-failed-ink");
    expect(screen.getByTestId("source-error-src-1")).toHaveTextContent("token refused (401)");
    expect(screen.getByTestId("source-state-new")).toHaveTextContent("Never synced");
    expect(screen.getByTestId("source-state-new").className).toContain("text-neutral-ink");
  });

  it("removing is the danger tone, syncing is secondary, adding is the card's primary action", () => {
    view([source]);
    expect(screen.getByRole("button", { name: "Remove Architecture site" }).className).toContain("text-failed-ink");
    expect(screen.getByRole("button", { name: "Sync now" }).className).toContain("bg-raised");
    expect(screen.getByRole("button", { name: "Add a source" }).className).toContain("bg-inverse");
  });

  it("a reader sees the sources and no gesture", () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <KnowledgeSources org="varga" slug="framework" sources={[source]} onChange={vi.fn()} canEdit={false} />
      </QueryClientProvider>,
    );
    expect(screen.getByTestId("knowledge-sources")).toHaveTextContent("Architecture site");
    expect(screen.queryByRole("button", { name: "Add a source" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Sync now" })).toBeNull();
  });
});
