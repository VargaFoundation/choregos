import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Suspense } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import KnowledgeBasePage from "@/app/knowledge/[slug]/page";
import KnowledgePage from "@/app/knowledge/page";
import { api } from "@/lib/api";
import { scopeLabel, statusSummary, stillConverting } from "@/lib/knowledge";
import type { KnowledgeBase } from "@/lib/types";

vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "varga" }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      knowledgeBases: vi.fn(),
      projects: vi.fn(),
      knowledgeBase: vi.fn(),
      createKnowledgeBase: vi.fn(),
      uploadKnowledgeDocument: vi.fn(),
      knowledgeDocument: vi.fn(),
      convertKnowledgeDocument: vi.fn(),
    },
  };
});

const base: KnowledgeBase = {
  slug: "architecture",
  name: "Architecture framework",
  scope: "projects",
  projects: ["billing-api"],
  groups: [],
  serving_mode: "files",
  status: "active",
  document_count: 2,
  documents_by_status: { ready: 1, failed: 1 },
  documents: [
    { id: "d1", filename: "zoning.pptx", title: "Network zoning principles", mime: "x", size_bytes: 29000, digest: "sha256:a", source: "upload", status: "ready" },
    { id: "d2", filename: "scan.pdf", title: "scan.pdf", mime: "application/pdf", size_bytes: 4000, digest: "sha256:b", source: "upload", status: "failed", error: "the document holds no text" },
  ],
};

function wrap(node: React.ReactNode) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <Suspense fallback="…">{node}</Suspense>
    </QueryClientProvider>,
  );
}

describe("what the knowledge pages say", () => {
  it("a scope in words, documents by status, and whether a conversion still runs", () => {
    expect(scopeLabel({ scope: "org", projects: [], groups: [] })).toBe("whole organisation");
    expect(scopeLabel({ scope: "groups", projects: [], groups: ["auditors"] })).toBe("groups: auditors");
    expect(statusSummary({ failed: 1, ready: 3 })).toBe("3 ready · 1 failed");
    expect(statusSummary({})).toBe("no document");
    expect(stillConverting({ documents_by_status: { ready: 2 } })).toBe(false);
    expect(stillConverting({ documents_by_status: { converting: 1 } })).toBe(true);
  });
});

describe("the knowledge page (S33-01)", () => {
  beforeEach(() => {
    vi.mocked(api.projects).mockResolvedValue({
      items: [
        { slug: "billing-api", name: "Billing API" },
        { slug: "checkout-web", name: "Checkout web" },
      ],
    } as never);
  });

  it("lists the bases with their scope and documents, and creates one", async () => {
    vi.mocked(api.knowledgeBases).mockResolvedValue([{ ...base, documents: null }]);
    vi.mocked(api.createKnowledgeBase).mockResolvedValue(base);
    wrap(<KnowledgePage />);
    const table = await screen.findByTestId("knowledge-bases");
    expect(table).toHaveTextContent("Architecture framework");
    expect(table).toHaveTextContent("Billing API");
    expect(within(table).getByRole("link", { name: "Billing API" })).toHaveAttribute("href", "/p/billing-api/knowledge");
    expect(table).toHaveTextContent("1 ready · 1 failed");

    fireEvent.change(screen.getByPlaceholderText("architecture-framework"), { target: { value: "security" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(api.createKnowledgeBase).toHaveBeenCalled());
    expect(vi.mocked(api.createKnowledgeBase).mock.calls[0]).toEqual([
      "varga",
      { slug: "security", name: "security", scope: "org", serving_mode: "files", projects: [], groups: [] },
    ]);
  });

  it("a base shows each document's status, the reason of a failure, and its markdown", async () => {
    vi.mocked(api.knowledgeBase).mockResolvedValue(base);
    vi.mocked(api.knowledgeDocument).mockResolvedValue({ ...base.documents![0]!, markdown: "# Network zoning principles\n" });
    await act(async () => {
      wrap(<KnowledgeBasePage params={Promise.resolve({ slug: "architecture" })} />);
    });
    const documents = await screen.findByTestId("knowledge-documents");
    expect(documents).toHaveTextContent("ready");
    expect(documents).toHaveTextContent("the document holds no text");
    expect(screen.getAllByRole("button", { name: "Convert again" })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Network zoning principles" }));
    expect(await screen.findByTestId("knowledge-markdown")).toHaveTextContent("# Network zoning principles");
  });

  it("says which sheets of a ready document were cut, in the list and where it is read (S42-09e)", async () => {
    const cut = { ...base.documents![0]!, warnings: ['Sheet "Q3": 1200 more rows not shown (the first 2000 are kept)'] };
    vi.mocked(api.knowledgeBase).mockResolvedValue({ ...base, documents: [cut] });
    vi.mocked(api.knowledgeDocument).mockResolvedValue({ ...cut, markdown: "## Q3\n" });
    await act(async () => {
      wrap(<KnowledgeBasePage params={Promise.resolve({ slug: "architecture" })} />);
    });
    expect(await screen.findByTestId(`document-warnings-${cut.id}`)).toHaveTextContent('Cut: Sheet "Q3": 1200 more rows not shown');
    fireEvent.click(screen.getByRole("button", { name: cut.title }));
    expect(await screen.findByTestId("knowledge-document-warning")).toHaveTextContent("1200 more rows not shown");
  });

  it("uploads each chosen file", async () => {
    vi.mocked(api.knowledgeBase).mockResolvedValue({ ...base, documents: [] });
    vi.mocked(api.uploadKnowledgeDocument).mockResolvedValue({ ...base.documents![0]!, status: "pending" });
    await act(async () => {
      wrap(<KnowledgeBasePage params={Promise.resolve({ slug: "architecture" })} />);
    });
    const input = await screen.findByLabelText("Documents to upload");
    const deck = new File(["x"], "zoning.pptx");
    fireEvent.change(input, { target: { files: [deck] } });
    await waitFor(() => expect(api.uploadKnowledgeDocument).toHaveBeenCalledWith("varga", "architecture", deck));
    expect(await screen.findByRole("status")).toHaveTextContent("zoning.pptx: pending");
  });
});

describe("the registry's scope (S39-14)", () => {
  const org: KnowledgeBase = { ...base, slug: "policy", name: "Security policy", scope: "org", projects: [] };
  const people: KnowledgeBase = { ...base, slug: "audit", name: "Audit notes", scope: "groups", projects: [], groups: ["auditors"] };
  beforeEach(() => {
    vi.mocked(api.projects).mockResolvedValue({
      items: [
        { slug: "billing-api", name: "Billing API" },
        { slug: "checkout-web", name: "Checkout web" },
      ],
    } as never);
    vi.mocked(api.knowledgeBases).mockResolvedValue([org, base, people].map((b) => ({ ...b, documents: null })));
    vi.mocked(api.createKnowledgeBase).mockResolvedValue(base);
    window.history.replaceState(null, "", "/knowledge");
  });

  it("filters by scope and says who uses each base", async () => {
    wrap(<KnowledgePage />);
    const table = await screen.findByTestId("knowledge-bases");
    expect(table).toHaveTextContent("Every project");
    expect(table).toHaveTextContent("People in groups auditors (never agents)");
    fireEvent.click(screen.getByRole("button", { name: "Group" }));
    expect(table).toHaveTextContent("Audit notes");
    expect(table).not.toHaveTextContent("Security policy");
    expect(window.location.search).toBe("?scope=groups");
    fireEvent.click(screen.getByRole("button", { name: "All" }));
    expect(table).toHaveTextContent("Security policy");
  });

  it("picks the projects from a list, not from typed slugs", async () => {
    wrap(<KnowledgePage />);
    await screen.findByTestId("knowledge-bases");
    fireEvent.change(screen.getByRole("combobox", { name: "Scope" }), { target: { value: "projects" } });
    expect(screen.queryByText(/comma-separated/)).toBeNull();
    fireEvent.click(await screen.findByRole("checkbox", { name: /Checkout web/ }));
    fireEvent.change(screen.getByPlaceholderText("architecture-framework"), { target: { value: "checkout-docs" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(api.createKnowledgeBase).toHaveBeenCalled());
    expect(vi.mocked(api.createKnowledgeBase).mock.calls[0]![1]).toMatchObject({ scope: "projects", projects: ["checkout-web"] });
    expect(screen.getByText(/create it from the project's Knowledge page/)).toBeInTheDocument();
  });
});
