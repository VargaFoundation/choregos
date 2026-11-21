import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { FilesCard } from "@/components/ticket/files-card";
import { api, ApiError } from "@/lib/api";
import type { Artifact } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, attachWorkItemFiles: vi.fn() } };
});

const file = (name: string, handed_by: Artifact["handed_by"]): Artifact => ({
  name,
  type: "pdf",
  kind: "report",
  media_type: "application/pdf",
  version: 1,
  digest: `sha256:${"a".repeat(64)}`,
  size: 2048,
  produced_by: { run_id: "" },
  created_at: "2026-11-20T10:00:00Z",
  handed_by,
});

const rule = { accept: ["pdf"], min: 0, max: 5 };
const artifacts = [file("quote-a.pdf", "person"), file("quote-b.pdf", "a2a"), file("report", null)];

beforeEach(() => vi.clearAllMocks());

describe("the Files card of a work item (ADR 0201)", () => {
  it("lists the handed files with who handed them and a download link, not the step outputs", () => {
    render(<FilesCard itemId="w1" artifacts={artifacts} rule={rule} terminal={false} onChanged={vi.fn()} />);
    const rows = screen.getByTestId("handed-files").querySelectorAll("li");
    expect(rows).toHaveLength(2);
    expect(rows[0]!.textContent).toContain("handed by a person");
    expect(rows[0]!.textContent).toContain("2.0 KiB");
    expect(rows[1]!.textContent).toContain("handed by an application");
    expect(screen.getByRole("link", { name: "quote-a.pdf" }).getAttribute("href")).toBe(
      "/api/v1/work-items/w1/artifacts/quote-a.pdf%401/content?download=true",
    );
    expect(screen.queryByText("report")).toBeNull();
  });

  it("hides Add files on a terminal work item", () => {
    const { rerender } = render(
      <FilesCard itemId="w1" artifacts={artifacts} rule={rule} terminal={false} onChanged={vi.fn()} />,
    );
    expect(screen.getByRole("button", { name: "Add files" })).toBeTruthy();
    rerender(<FilesCard itemId="w1" artifacts={artifacts} rule={rule} terminal onChanged={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Add files" })).toBeNull();
    expect(screen.getByTestId("handed-files")).toBeTruthy();
  });

  it("sends the chosen files and shows a refusal", async () => {
    const onChanged = vi.fn();
    vi.mocked(api.attachWorkItemFiles).mockRejectedValueOnce(
      new ApiError(409, { title: "Conflict", detail: "the work item is finished" } as never),
    );
    render(<FilesCard itemId="w1" artifacts={[]} rule={rule} terminal={false} onChanged={onChanged} />);
    fireEvent.click(screen.getByRole("button", { name: "Add files" }));
    fireEvent.change(screen.getByLabelText("Files"), { target: { files: [new File(["%PDF"], "c.pdf")] } });
    fireEvent.click(screen.getByRole("button", { name: "Send the files" }));
    expect(await screen.findByText(/the work item is finished/)).toBeTruthy();
    expect(onChanged).not.toHaveBeenCalled();
    vi.mocked(api.attachWorkItemFiles).mockResolvedValueOnce([]);
    fireEvent.click(screen.getByRole("button", { name: "Send the files" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });
});
