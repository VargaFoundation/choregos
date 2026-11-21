import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import type { Action } from "@/lib/types";
import { pageOf } from "./page";

const rows = (from: number, count: number) =>
  Array.from({ length: count }, (_, i) => ({ id: `a-${from + i}` }) as unknown as Action);

describe("the inbox reads the pending actions page after page, up to a cap (ADR 0206)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("follows the cursor to the end and asks the server for the pending ones only", async () => {
    const spy = vi
      .spyOn(api, "orgActions")
      .mockResolvedValueOnce(pageOf(rows(0, 200), "c1"))
      .mockResolvedValueOnce(pageOf(rows(200, 30)));
    const all = await api.pendingOrgActions("acme", "billing-api");
    expect(all.items).toHaveLength(230);
    expect(all.meta.has_more).toBe(false);
    expect(spy.mock.calls[0]![1]).toMatchObject({ status: "pending_approval", project: "billing-api", limit: 200 });
    expect(spy.mock.calls[1]![1]).toMatchObject({ cursor: "c1" });
  });

  it("stops at the cap and says there is more, so the count is shown as a lower bound", async () => {
    const spy = vi.spyOn(api, "orgActions").mockImplementation(async (_org, params) =>
      pageOf(rows(Number(params?.cursor ?? 0), 200), String(Number(params?.cursor ?? 0) + 200)),
    );
    const all = await api.pendingOrgActions("acme");
    expect(all.items).toHaveLength(1000);
    expect(all.meta.has_more).toBe(true);
    expect(spy).toHaveBeenCalledTimes(5);
  });
});
