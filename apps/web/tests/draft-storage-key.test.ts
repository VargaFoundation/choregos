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
const OLD_KEY = "choregos.brouillon.p.w"; // lang: fr-ok alias:external

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return createElement(QueryClientProvider, { client }, createElement(DraftProvider, { slug: "p", name: "w" } as ComponentProps<typeof DraftProvider>, children));
}

beforeEach(() => {
  vi.mocked(api.workflowNamed).mockResolvedValue({ name: "w", version: 3, yaml: BASE, is_active: true, json: {} } as never);
  vi.mocked(api.validateWorkflow).mockResolvedValue({ valid: true, errors: [], warnings: [], graph: undefined, process: [] } as never);
});

describe("the draft's storage key", () => {
  it("is English", () => {
    expect(draftKey("p", "w")).toBe(NEW_KEY);
  });

  it("a draft saved under the former key is restored and moved", async () => {
    // What a tab kept before the key and the fields were renamed (S45-27).
    window.sessionStorage.setItem(
      OLD_KEY,
      JSON.stringify({
        yaml: EDITED,
        resultat: null, // lang: fr-ok alias:external
        diff: "",
        gestes: [{ texteAvant: BASE }], // lang: fr-ok alias:external
        versionDeBase: 3, // lang: fr-ok alias:external
        frappeEnCours: false, // lang: fr-ok alias:external
      }),
    );
    const { result } = renderHook(() => useDraft(), { wrapper });
    await waitFor(() => expect(result.current.yaml).toBe(EDITED));
    expect(result.current.pending).toBe(1);
    expect(result.current.resumed).toBe(true);
    await waitFor(() => expect(window.sessionStorage.getItem(NEW_KEY)).not.toBeNull());
    expect(window.sessionStorage.getItem(OLD_KEY)).toBeNull();
    expect(JSON.parse(window.sessionStorage.getItem(NEW_KEY)!)).toMatchObject({ yaml: EDITED, baseVersion: 3, gestures: [{ textBefore: BASE }] });
  });

  it("a draft already under the new key wins, and the former key is left alone", async () => {
    window.sessionStorage.setItem(NEW_KEY, JSON.stringify({ yaml: EDITED, result: null, diff: "", gestures: [{ textBefore: BASE }], baseVersion: 3, typing: false }));
    window.sessionStorage.setItem(OLD_KEY, JSON.stringify({ yaml: "other\n", gestes: [], versionDeBase: 1 })); // lang: fr-ok alias:external
    const { result } = renderHook(() => useDraft(), { wrapper });
    await waitFor(() => expect(result.current.yaml).toBe(EDITED));
  });
});
