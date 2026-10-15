import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { KnowledgeSources } from "@/components/knowledge-sources";
import { api } from "@/lib/api";
import { sourceConfig, syncSummary } from "@/lib/knowledge";
import type { KnowledgeSource } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, createKnowledgeSource: vi.fn(), syncKnowledgeSource: vi.fn(), deleteKnowledgeSource: vi.fn() } };
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
        interval_minutes: 60,
      }),
    );
  });

  it("adds a Confluence space or page tree through its connector (S39-20)", async () => {
    vi.mocked(api.createKnowledgeSource).mockResolvedValue(source);
    render(<KnowledgeSources org="varga" slug="framework" sources={[]} onChange={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("source kind"), { target: { value: "confluence" } });
    expect(screen.queryByLabelText("site or drive id")).toBeNull();
    fireEvent.change(screen.getByLabelText("documents connector"), { target: { value: "wiki" } });
    fireEvent.change(screen.getByLabelText("space key"), { target: { value: "ENG" } });
    fireEvent.change(screen.getByLabelText("page id"), { target: { value: " 12345 " } });
    fireEvent.click(screen.getByRole("button", { name: "add source" }));
    await waitFor(() =>
      expect(api.createKnowledgeSource).toHaveBeenCalledWith("varga", "framework", {
        kind: "confluence",
        name: "Confluence space or page tree",
        connector: "wiki",
        config: { space_key: "ENG", page_id: "12345", include_attachments: false },
        interval_minutes: 60,
      }),
    );
    expect(screen.getByText(/everything the connector/)).toBeInTheDocument();
  });

  it("sends no page id for a whole space, and asks for attachments when ticked", () => {
    const base = { site: "", folder: "", url: "", ref: "", paths: "" };
    expect(sourceConfig("confluence", { ...base, space: "ENG", pageId: "" })).toEqual({ space_key: "ENG", include_attachments: false });
    expect(sourceConfig("confluence", { ...base, space: "ENG", attachments: true })).toEqual({ space_key: "ENG", include_attachments: true });
  });
});
