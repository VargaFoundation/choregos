import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { KnowledgeSources } from "@/components/knowledge-sources";
import { api } from "@/lib/api";
import { sourceConfig, syncSummary } from "@/lib/knowledge";
import type { KnowledgeSource, OrgConnector } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, orgConnectors: vi.fn(), createKnowledgeSource: vi.fn(), syncKnowledgeSource: vi.fn(), deleteKnowledgeSource: vi.fn() } };
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
    expect(sourceConfig("git", fields)).toEqual({ url: "https://git.test/r.git", ref: "main", paths: ["docs/*.md", "*.md"] });
  });
});

describe("the sources of a base (S33-04)", () => {
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
});
