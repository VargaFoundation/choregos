import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { KnowledgeSources } from "@/components/knowledge-sources";
import { api } from "@/lib/api";
import { nextSyncLabel, refreshBody, refreshLabel, sourceConfig, syncSummary } from "@/lib/knowledge";
import type { KnowledgeSource } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, createKnowledgeSource: vi.fn(), updateKnowledgeSource: vi.fn(), syncKnowledgeBase: vi.fn(), syncKnowledgeSource: vi.fn(), deleteKnowledgeSource: vi.fn() } };
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

describe("what a source says", () => {
  it("summarises a sync and builds its configuration", () => {
    expect(syncSummary({ added: 2, deleted: 1, unchanged: 9 })).toBe("2 added · 1 deleted");
    expect(syncSummary({ unchanged: 3 })).toBe("no change");
    expect(syncSummary(null)).toBe("never synced");
    const fields = { site: "b!drive", folder: "Policies", url: "https://git.test/r.git", ref: "", paths: "docs/*.md, *.md" };
    expect(sourceConfig("sharepoint", fields)).toEqual({ drive_id: "b!drive", folder: "Policies" });
    expect(sourceConfig("git", fields)).toEqual({ url: "https://git.test/r.git", ref: "main", paths: ["docs/*.md", "*.md"] });
  });
});

describe("the sources of a base (S33-04)", () => {
  it("shows when each last synced, what it did and why it failed; syncs on demand", async () => {
    vi.mocked(api.syncKnowledgeSource).mockResolvedValue(source);
    const onChange = vi.fn();
    render(<KnowledgeSources org="varga" slug="framework" sources={[source]} onChange={onChange} />);
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
    render(<KnowledgeSources org="varga" slug="framework" sources={[]} onChange={vi.fn()} />);
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

  it("says how each source refreshes and when it syncs next", () => {
    const cron: KnowledgeSource = {
      ...source,
      refresh_mode: "cron",
      refresh_cron: "0 6 * * 1-5",
      refresh_timezone: "Europe/Paris",
      next_sync_at: new Date(Date.now() + 23 * 60_000).toISOString(),
    };
    render(<KnowledgeSources org="varga" slug="framework" sources={[cron]} onChange={vi.fn()} />);
    expect(screen.getByTestId("knowledge-sources")).toHaveTextContent("cron 0 6 * * 1-5 (Europe/Paris) · next in 23 minutes");
    expect(refreshLabel({ ...source, refresh_mode: "manual" })).toBe("manual");
    expect(nextSyncLabel({ refresh_mode: "manual" })).toBe("—");
    expect(refreshBody({ mode: "manual", interval: "5", cron: "", timezone: "UTC" })).toEqual({ refresh_mode: "manual" });
  });

  it("edits the refresh of a source, or of all of them", async () => {
    vi.mocked(api.updateKnowledgeSource).mockResolvedValue(source);
    const other = { ...source, id: "src-2", name: "Other" };
    render(<KnowledgeSources org="varga" slug="framework" sources={[source, other]} onChange={vi.fn()} />);
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
    render(<KnowledgeSources org="varga" slug="framework" sources={[source]} onChange={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "edit refresh" }));
    fireEvent.change(within(screen.getByRole("form", { name: "edit refresh" })).getByLabelText("refresh mode"), { target: { value: "cron" } });
    fireEvent.change(within(screen.getByRole("form", { name: "edit refresh" })).getByLabelText("cron expression"), { target: { value: "*/1 * * * *" } });
    fireEvent.click(screen.getByRole("button", { name: "save refresh" }));
    expect(await screen.findByText(/the minimum is 5/)).toBeInTheDocument();
  });

  it("syncs a whole base at once", async () => {
    vi.mocked(api.syncKnowledgeBase).mockResolvedValue({} as never);
    render(<KnowledgeSources org="varga" slug="framework" sources={[source]} onChange={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "sync all now" }));
    await waitFor(() => expect(api.syncKnowledgeBase).toHaveBeenCalledWith("varga", "framework"));
  });
});
