import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api";

describe("an API error without an explanation", () => {
  it("says itself in English, with its HTTP status", () => {
    expect(new ApiError(502, {}).message).toBe("request failed (HTTP 502)");
  });

  it("keeps the detail or the title the server gives", () => {
    expect(new ApiError(422, { title: "Unprocessable", detail: "metadata.name differs" }).message).toBe("metadata.name differs");
    expect(new ApiError(404, { title: "Not found" }).message).toBe("Not found");
  });
});
