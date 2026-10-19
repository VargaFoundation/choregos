import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { mergeConfig, useProjectConfigPatch, type ProjectConfigValue } from "@/lib/project-config";

const { api } = vi.hoisted(() => ({ api: { project: vi.fn(), updateProject: vi.fn() } }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, ...api } };
});

const config = {
  slug: "dev",
  org: "acme",
  repo: { url: "https://github.com/acme/dev.git", default_branch: "main", clone_depth: 20 },
  envs: ["dev", "prod"],
  agent: { default_backend: "claude-code", allowed_backends: ["claude-code"] },
  notify: { emails: ["po@acme.dev"] },
  tools: ["web_search"],
  groups: ["dev", "rh"],
  labels: { team: "platform" },
  dod: { facts: { signed: "test -f SIGNED" } },
  schedules: [{ name: "weekly", cron: "0 6 * * mon", title: "Weekly" }],
} as unknown as ProjectConfigValue;

describe("a section's change to the project configuration (S40-06c)", () => {
  it("keeps every key the change does not name", () => {
    const merged = mergeConfig(config, { notify: { slack_channel: "#dev" } });
    expect(merged).toEqual({ ...config, notify: { slack_channel: "#dev" } });
    expect(merged.groups).toEqual(["dev", "rh"]);
    expect(merged.schedules).toHaveLength(1);
    expect(merged.dod).toEqual({ facts: { signed: "test -f SIGNED" } });
  });

  it("removes a key set to undefined, and nothing else", () => {
    const merged = mergeConfig(config, { labels: undefined });
    expect("labels" in merged).toBe(false);
    expect(Object.keys(merged).sort()).toEqual(Object.keys(config).filter((k) => k !== "labels").sort());
  });

  it("takes a function of the LATEST configuration, so a nested change keeps its siblings", () => {
    const merged = mergeConfig(config, (latest) => ({ repo: { ...latest.repo!, test_command: "pytest -q" } }));
    expect(merged.repo).toEqual({ ...config.repo, test_command: "pytest -q" });
  });
});

describe("useProjectConfigPatch", () => {
  beforeEach(() => vi.resetAllMocks());
  function wrapper(client: QueryClient) {
    const Wrapper = ({ children }: { children: ReactNode }) => createElement(QueryClientProvider, { client }, children);
    return Wrapper;
  }

  it("reads the project again, merges into THAT, sends the whole configuration, and refreshes project and readiness", async () => {
    // The page loaded an older configuration; someone changed the labels since. The patch must not undo that.
    api.project.mockResolvedValue({ name: "Dev", config: { ...config, labels: { team: "core" } } });
    api.updateProject.mockResolvedValue({});
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useProjectConfigPatch("dev"), { wrapper: wrapper(client) });
    let ok = false;
    await act(async () => {
      ok = await result.current.patch({ notify: { slack_channel: "#dev" } }, { saved: "Notification settings saved" });
    });
    expect(ok).toBe(true);
    expect(api.updateProject).toHaveBeenCalledWith("dev", { config: { ...config, labels: { team: "core" }, notify: { slack_channel: "#dev" } } });
    // `groups` is sent as it was: only a holder of tools:grant may change it.
    expect(api.updateProject.mock.calls[0]![1].config.groups).toEqual(["dev", "rh"]);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["project", "dev"] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["readiness", "dev"] });
    expect(result.current.message).toBe("Notification settings saved");
  });

  it("sends the name with the configuration, and reports a refusal without refreshing", async () => {
    api.project.mockResolvedValue({ name: "Dev", config });
    api.updateProject.mockRejectedValue(new Error("422: unknown backend"));
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useProjectConfigPatch("dev"), { wrapper: wrapper(client) });
    let ok = true;
    await act(async () => {
      ok = await result.current.patch({ tools: [] }, { name: "Dev 2" });
    });
    expect(ok).toBe(false);
    expect(api.updateProject.mock.calls[0]![1].name).toBe("Dev 2");
    expect(result.current.error).toBe("422: unknown backend");
    expect(invalidate).not.toHaveBeenCalled();
  });
});
