import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DeleteAgent } from "@/components/agents/lifecycle";
import { ApiError, api } from "@/lib/api";
import type { Agent } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, deleteAgent: vi.fn() } };
});

const agent = (can: Partial<NonNullable<Agent["can"]>>, scope: Agent["scope"] = "org"): Agent =>
  ({ slug: "l-k-jn", kind: "internal", display_name: "L K JN", status: "active", latest_version: 1, scope, can }) as Agent;

function wrap(node: React.ReactNode) {
  return render(<QueryClientProvider client={new QueryClient()}>{node}</QueryClientProvider>);
}

describe("deleting an agent", () => {
  beforeEach(() => {
    vi.mocked(api.deleteAgent).mockReset();
  });

  it("is offered only to whoever may manage it, never on a platform agent", () => {
    const { rerender } = wrap(<DeleteAgent org="acme" agent={agent({ manage: false })} onDeleted={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Delete…" })).not.toBeInTheDocument();
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <DeleteAgent org="acme" agent={agent({ manage: true }, "platform")} onDeleted={vi.fn()} />
      </QueryClientProvider>,
    );
    expect(screen.queryByRole("button", { name: "Delete…" })).not.toBeInTheDocument();
  });

  it("deletes after confirmation and leaves the page", async () => {
    vi.mocked(api.deleteAgent).mockResolvedValue(undefined);
    const onDeleted = vi.fn();
    wrap(<DeleteAgent org="acme" agent={agent({ manage: true })} onDeleted={onDeleted} />);
    fireEvent.click(screen.getByRole("button", { name: "Delete…" }));
    expect(screen.getByTestId("confirmation")).toHaveTextContent("Only an agent that never served can be deleted");
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Delete l-k-jn" })));
    expect(api.deleteAgent).toHaveBeenCalledWith("acme", "l-k-jn");
    expect(onDeleted).toHaveBeenCalled();
  });

  it("a refusal stays on screen with its reasons", async () => {
    vi.mocked(api.deleteAgent).mockImplementation(async () => {
      throw new ApiError(409, { detail: "agent `l-k-jn` cannot be deleted: it has run 3 times. Retire it instead" });
    });
    const onDeleted = vi.fn();
    wrap(<DeleteAgent org="acme" agent={agent({ manage: true })} onDeleted={onDeleted} />);
    fireEvent.click(screen.getByRole("button", { name: "Delete…" }));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Delete l-k-jn" })));
    expect(await screen.findByRole("alert")).toHaveTextContent("cannot be deleted: it has run 3 times. Retire it instead");
    expect(onDeleted).not.toHaveBeenCalled();
  });
});
