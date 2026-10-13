import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { KnowledgeIndex } from "@/components/knowledge-index";
import { api } from "@/lib/api";
import { formatEvalSet, parseEvalSet, recallLabel, searchModeLabel } from "@/lib/knowledge";
import type { KnowledgeBase } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: { ...real.api, searchKnowledgeBase: vi.fn(), putKnowledgeEvalSet: vi.fn(), runKnowledgeEval: vi.fn(), reindexKnowledgeBase: vi.fn() },
  };
});

const base: KnowledgeBase = {
  slug: "framework",
  name: "Framework",
  scope: "org",
  serving_mode: "index",
  status: "active",
  index: { chunks: 12, documents: 5, mode: "full-text" },
  eval_set: [{ question: "Which TLS version?", expected: ["encryption.docx"] }],
  eval_result: {
    k: 5,
    recall: 0.8,
    mode: "full-text",
    ran_at: "2026-10-12T13:00:00",
    cases: [{ question: "Which TLS version?", expected: ["encryption.docx"], found: ["encryption.docx"], returned: ["encryption.docx"] }],
  },
};

describe("the eval set and the search mode, in words", () => {
  it("parses one question per line and writes it back", () => {
    const cases = parseEvalSet("Which TLS? | encryption.docx, policy.pdf\nno pipe here\n | empty");
    expect(cases).toEqual([{ question: "Which TLS?", expected: ["encryption.docx", "policy.pdf"] }]);
    expect(formatEvalSet(cases)).toBe("Which TLS? | encryption.docx, policy.pdf");
  });

  it("says when pgvector is missing, and what was not measured", () => {
    expect(searchModeLabel("full-text")).toContain("pgvector is not installed");
    expect(searchModeLabel("hybrid")).toContain("embeddings");
    expect(recallLabel(0.8)).toBe("80 %");
    expect(recallLabel(null)).toBe("not measured");
  });
});

describe("an index base (S33-03)", () => {
  it("shows its state, its recall@5, and searches with citations", async () => {
    vi.mocked(api.searchKnowledgeBase).mockResolvedValue({
      mode: "full-text",
      hits: [
        {
          knowledge_base: "framework",
          document_id: "d1",
          document: "Encryption standard",
          filename: "encryption.docx",
          heading_path: ["Encryption standard", "In transit"],
          excerpt: "TLS 1.3 is required.",
          score: 0.03,
        },
      ],
    });
    const onChange = vi.fn();
    render(<KnowledgeIndex org="varga" base={base} onChange={onChange} />);
    expect(screen.getByTestId("knowledge-index-state")).toHaveTextContent("5 document(s) in 12 chunks · full-text only");
    expect(screen.getByTestId("knowledge-recall")).toHaveTextContent("recall@5: 80 %");
    fireEvent.change(screen.getByLabelText("Search the knowledge base"), { target: { value: "TLS" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    const hits = await screen.findByTestId("knowledge-hits");
    expect(hits).toHaveTextContent("Encryption standard");
    expect(hits).toHaveTextContent("encryption.docx — Encryption standard > In transit");
    expect(api.searchKnowledgeBase).toHaveBeenCalledWith("varga", "framework", "TLS");
  });

  it("saves the eval set and measures recall", async () => {
    vi.mocked(api.putKnowledgeEvalSet).mockResolvedValue(base);
    vi.mocked(api.runKnowledgeEval).mockResolvedValue(base);
    render(<KnowledgeIndex org="varga" base={base} onChange={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Eval set"), { target: { value: "Who owns backups? | owners.xlsx" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(api.putKnowledgeEvalSet).toHaveBeenCalledWith("varga", "framework", [{ question: "Who owns backups?", expected: ["owners.xlsx"] }]),
    );
    fireEvent.click(screen.getByRole("button", { name: "Measure recall" }));
    await waitFor(() => expect(api.runKnowledgeEval).toHaveBeenCalledWith("varga", "framework"));
  });
});
