import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";
import PlatformPage from "@/app/admin/platform/page";
import { api, ApiError } from "@/lib/api";
import type { AgentBackendInfo, ExecutorInfo } from "@/lib/types";

/** The Platform page: what a backend and an executor are, and what an administrator may change (S42-07). */
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: { ...real.api, backends: vi.fn(), executors: vi.fn(), putPlatformBackend: vi.fn() },
  };
});

const backends: AgentBackendInfo[] = [
  {
    name: "claude-code",
    enabled: true,
    version: "2.1.0",
    capabilities: ["acp", "mcp"],
    conformance: { passed: 23, total: 24, last_run_at: "2026-10-15T10:00:00Z", report_url: "https://r.test/1", failures: ["structured_output"] },
  },
  { name: "codex", enabled: false, capabilities: ["acp"], disabled_reason: "conformance below threshold", conformance: { failures: [] } },
];
const kinds = ["agent_sandbox", "k8s_job", "tekton", "aca", "local_docker"] as const;
const executors: ExecutorInfo[] = kinds.map((kind) => ({ kind, enabled: kind === "agent_sandbox" }));

function show() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <PlatformPage />
    </QueryClientProvider>,
  );
}

describe("the Platform page", () => {
  beforeEach(() => {
    vi.mocked(api.backends).mockResolvedValue(backends);
    vi.mocked(api.executors).mockResolvedValue(executors);
    vi.mocked(api.putPlatformBackend).mockReset().mockResolvedValue(backends[0]!);
  });

  it("explains what a backend and an executor are, and where they are set", async () => {
    show();
    expect(await screen.findByText(/coding agent CLI that the runner image ships/)).toBeInTheDocument();
    expect(screen.getByText(/counted as usable by readiness/)).toBeInTheDocument();
    expect(screen.getByText("CHOREGOS_ALLOWED_AGENT_BACKENDS")).toBeInTheDocument();
    expect(screen.getByText(/place where an agent stage runs/)).toBeInTheDocument();
    expect(screen.getByText("CHOREGOS_EXECUTOR_KIND")).toBeInTheDocument();
    expect(screen.getByText("global.runner.allowedExecutors")).toBeInTheDocument();
  });

  it("shows the capabilities, the conformance and the reason a backend is off", async () => {
    show();
    const claude = await screen.findByTestId("backend-claude-code");
    expect(within(claude).getByText(/Capabilities: acp, mcp/)).toBeInTheDocument();
    expect(within(claude).getByText(/23\/24 passed/)).toBeInTheDocument();
    expect(within(claude).getByText(/structured_output/)).toBeInTheDocument();
    expect(within(claude).getByRole("link", { name: "Report" })).toHaveAttribute("href", "https://r.test/1");
    const codex = screen.getByTestId("backend-codex");
    expect(within(codex).getByText(/conformance below threshold/)).toBeInTheDocument();
    expect(within(codex).queryByText(/Conformance:/)).toBeNull();
  });

  it("will not disable a backend without a reason, then sends the update", async () => {
    show();
    const claude = await screen.findByTestId("backend-claude-code");
    fireEvent.click(within(claude).getByRole("button", { name: "Disable claude-code" }));
    const confirm = within(claude).getByRole("button", { name: "Confirm disable" });
    expect(confirm).toBeDisabled();
    fireEvent.click(confirm);
    expect(api.putPlatformBackend).not.toHaveBeenCalled();
    fireEvent.change(within(claude).getByLabelText("Why disable claude-code?"), { target: { value: " cost spike " } });
    fireEvent.click(within(claude).getByRole("button", { name: "Confirm disable" }));
    await waitFor(() =>
      expect(api.putPlatformBackend).toHaveBeenCalledWith({ name: "claude-code", enabled: false, disabled_reason: "cost spike" }),
    );
  });

  it("enables a disabled backend in one click", async () => {
    show();
    const codex = await screen.findByTestId("backend-codex");
    fireEvent.click(within(codex).getByRole("button", { name: "Enable codex" }));
    await waitFor(() =>
      expect(api.putPlatformBackend).toHaveBeenCalledWith({ name: "codex", enabled: true, disabled_reason: null }),
    );
  });

  it("tells a non-administrator that only a platform administrator can change this", async () => {
    vi.mocked(api.putPlatformBackend).mockRejectedValue(new ApiError(403, { title: "Forbidden" }));
    show();
    const codex = await screen.findByTestId("backend-codex");
    fireEvent.click(within(codex).getByRole("button", { name: "Enable codex" }));
    expect(await within(codex).findByText("Only a platform administrator can change this.")).toBeInTheDocument();
  });

  it("explains each executor, links its procedure and offers no control", async () => {
    show();
    for (const kind of kinds) {
      const row = await screen.findByTestId(`executor-${kind}`);
      expect(within(row).getByRole("link", { name: "Set-up procedure" })).toHaveAttribute(
        "href",
        expect.stringContaining(`docs/procedures/connector.runtime.${kind}.md`),
      );
      expect(within(row).queryByRole("button")).toBeNull();
      expect(existsSync(resolve(__dirname, `../../../docs/procedures/connector.runtime.${kind}.md`))).toBe(true);
    }
    expect(screen.getByText(/Change it in the deployment/)).toBeInTheDocument();
  });
});
