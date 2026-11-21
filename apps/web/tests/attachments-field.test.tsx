import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NewWorkItemForm } from "@/components/workflows/new-work-item";
import { api, ApiError } from "@/lib/api";
import { attachmentRule, handedFiles } from "@/lib/attachments";
import type { Artifact } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      workflowNamed: vi.fn(),
      validateWorkflow: vi.fn().mockResolvedValue({ valid: true, graph: { nodes: [], edges: [] } }),
      createWorkItem: vi.fn(),
      attachWorkItemFiles: vi.fn(),
      action: vi.fn(),
    },
  };
});

const withFiles = { name: "compare", version: 1, yaml: "x", json: { metadata: { attachments: { accept: ["pdf"], min: 2, max: 3 } } } };
const without = { name: "plain", version: 1, yaml: "x", json: { metadata: {} } };

function form(onDone = vi.fn()) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <NewWorkItemForm
        slug="billing-api"
        workflows={["compare"]}
        initial="compare"
        tracker={{ external: false, type: "internal" }}
        onDone={onDone}
      />
    </QueryClientProvider>,
  );
  return onDone;
}

const pdf = (name: string) => new File(["%PDF-1.7"], name, { type: "application/pdf" });

function choose(...files: File[]) {
  fireEvent.change(screen.getByLabelText("Files"), { target: { files } });
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.createWorkItem).mockResolvedValue({ id: "w9", tracker_key: "BIL-9" } as never);
  vi.mocked(api.attachWorkItemFiles).mockResolvedValue([]);
  vi.mocked(api.action).mockResolvedValue(undefined);
});

describe("the file control of the new work item form (ADR 0201)", () => {
  it("appears only for a workflow that declares attachments, and states the bounds", async () => {
    vi.mocked(api.workflowNamed).mockResolvedValue(without as never);
    const { unmount } = render(
      <QueryClientProvider client={new QueryClient()}>
        <NewWorkItemForm slug="p" workflows={["plain"]} initial="plain" tracker={{ external: false, type: "internal" }} onDone={vi.fn()} />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(api.workflowNamed).toHaveBeenCalled());
    expect(screen.queryByTestId("attachments-field")).toBeNull();
    unmount();
    vi.mocked(api.workflowNamed).mockResolvedValue(withFiles as never);
    form();
    expect((await screen.findByTestId("attachments-rule")).textContent).toBe(
      "Accepted types: pdf; at least 2 and at most 3 files.",
    );
  });

  it("is disabled under min, then files, attaches and starts in that order", async () => {
    vi.mocked(api.workflowNamed).mockResolvedValue(withFiles as never);
    const onDone = form();
    await screen.findByTestId("attachments-field");
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Compare" } });
    const button = screen.getByRole("button", { name: "Create and start" });
    choose(pdf("a.pdf"));
    expect(button).toBeDisabled();
    choose(pdf("b.pdf"));
    expect(button).toBeEnabled();
    fireEvent.click(button);
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(vi.mocked(api.createWorkItem).mock.calls[0]![1]).toMatchObject({ start: false });
    expect(vi.mocked(api.attachWorkItemFiles)).toHaveBeenCalledWith("w9", [expect.any(File), expect.any(File)]);
    expect(vi.mocked(api.action)).toHaveBeenCalledWith("w9", "mark_agent_ready");
    const order = [api.createWorkItem, api.attachWorkItemFiles, api.action].map(
      (call) => vi.mocked(call).mock.invocationCallOrder[0]!,
    );
    expect(order).toEqual([...order].sort((a, b) => a - b));
  });

  it("refuses a type the workflow does not accept", async () => {
    vi.mocked(api.workflowNamed).mockResolvedValue(withFiles as never);
    form();
    await screen.findByTestId("attachments-field");
    choose(new File(["x"], "notes.exe"));
    expect((await screen.findByRole("alert")).textContent).toContain("not accepted");
    expect(screen.queryByTestId("attachments-chosen")).toBeNull();
  });

  it("shows the API's refusal and the item's link, and resumes without creating twice", async () => {
    vi.mocked(api.workflowNamed).mockResolvedValue(withFiles as never);
    vi.mocked(api.attachWorkItemFiles).mockRejectedValueOnce(
      new ApiError(422, { title: "Unprocessable", detail: "b.pdf is not a PDF" } as never),
    );
    const onDone = form();
    await screen.findByTestId("attachments-field");
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Compare" } });
    choose(pdf("a.pdf"), pdf("b.pdf"));
    fireEvent.click(screen.getByRole("button", { name: "Create and start" }));
    expect(await screen.findByText(/b\.pdf is not a PDF/)).toBeTruthy();
    expect(screen.getByRole("link", { name: /BIL-9/ }).getAttribute("href")).toBe("/p/billing-api/items/w9");
    expect(onDone).not.toHaveBeenCalled();
    expect(api.action).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Send the rest" }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(api.createWorkItem).toHaveBeenCalledTimes(1);
    expect(api.attachWorkItemFiles).toHaveBeenCalledTimes(2);
  });
});

describe("attachments helpers", () => {
  it("fills the defaults and lists the latest handed revision of each name", () => {
    expect(attachmentRule({ attachments: { accept: ["pdf"] } })).toEqual({ accept: ["pdf"], min: 0, max: 20 });
    expect(attachmentRule({})).toBeNull();
    const make = (name: string, version: number, handed_by: Artifact["handed_by"]) =>
      ({ name, version, handed_by }) as Artifact;
    expect(handedFiles([make("a.pdf", 1, "person"), make("a.pdf", 2, "person"), make("out", 1, null)]).map((f) => f.version)).toEqual([2]);
  });
});
