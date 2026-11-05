import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { type ComponentProps, createElement, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DraftProvider, draftKey, useDraft } from "@/components/workflows/draft";
import { api } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      workflowNamed: vi.fn(),
      validateWorkflow: vi.fn(),
    },
  };
});

const BASE = "metadata: { name: w }\n";
const EDITED = "metadata: { name: w }\n# edited\n";
const NEW_KEY = "choregos.draft.p.w";
const OLD_KEY = "choregos.brouillon.p.w"; // lang: fr-ok - the former key, asserted ignored

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return createElement(QueryClientProvider, { client }, createElement(DraftProvider, { slug: "p", name: "w" } as ComponentProps<typeof DraftProvider>, children));
}

beforeEach(() => {
  window.sessionStorage.clear();
  vi.mocked(api.workflowNamed).mockResolvedValue({ name: "w", version: 3, yaml: BASE, is_active: true, json: {} } as never);
  vi.mocked(api.validateWorkflow).mockResolvedValue({ valid: true, errors: [], warnings: [], graph: undefined, process: [] } as never);
});

describe("the draft's storage key", () => {
  it("is English", () => {
    expect(draftKey("p", "w")).toBe(NEW_KEY);
  });

  it("a draft saved under the former key is not read any more, and left alone", async () => {
    // What a tab kept before the key and the fields were renamed (S45-27); removed in 0.24.0 (S45-49).
    const former = JSON.stringify({ yaml: EDITED, diff: "", versionDeBase: 3, gestes: [{ texteAvant: BASE }] }); // lang: fr-ok - removed, asserted gone
    window.sessionStorage.setItem(OLD_KEY, former);
    const { result } = renderHook(() => useDraft(), { wrapper });
    await waitFor(() => expect(result.current.yaml).toBe(BASE));
    expect(result.current.pending).toBe(0);
    expect(result.current.resumed).toBe(false);
    expect(window.sessionStorage.getItem(NEW_KEY)).toBeNull();
    expect(window.sessionStorage.getItem(OLD_KEY)).toBe(former);
  });

  it("a draft under the new key is resumed", async () => {
    window.sessionStorage.setItem(NEW_KEY, JSON.stringify({ yaml: EDITED, result: null, diff: "", gestures: [{ textBefore: BASE }], baseVersion: 3, typing: false }));
    const { result } = renderHook(() => useDraft(), { wrapper });
    await waitFor(() => expect(result.current.yaml).toBe(EDITED));
    expect(result.current.resumed).toBe(true);
  });
});
